import cv2
import os
import numpy as np
import re
import base64
from flask import Flask, render_template, Response, request, jsonify, session, redirect, url_for, send_from_directory
from database.db import db
from core.engine import engine
import time
import threading

app = Flask(__name__)
app.secret_key = 'smart_attend_secret_key'

# Runtime camera strategy:
# - server  => existing OpenCV webcam stream on server machine (default, current behavior)
# - browser => client browser captures frames and posts them for recognition (cloud-friendly)
_camera_mode_env = os.getenv('CAMERA_MODE', '').strip().lower()
if _camera_mode_env in ('server', 'browser'):
    CAMERA_MODE = _camera_mode_env
else:
    # Render free instances do not have physical webcams, so browser mode is the safe default there.
    on_render = bool(os.getenv('RENDER')) or bool(os.getenv('RENDER_EXTERNAL_HOSTNAME'))
    CAMERA_MODE = 'browser' if on_render else 'server'

print(f"[CAMERA] Runtime mode: {CAMERA_MODE}")

@app.route('/debug_routes')
def debug_routes():
    import urllib
    output = []
    for rule in app.url_map.iter_rules():
        options = {}
        for arg in rule.arguments:
            options[arg] = "[{0}]".format(arg)
        methods = ','.join(rule.methods)
        url = urllib.parse.unquote(url_for(rule.endpoint, **options))
        output.append(f"{url} ({methods}) -> {rule.endpoint}")
    return "<br>".join(sorted(output))

class AppState:
    def __init__(self):
        self.mode = 'idle' 
        self.active_params = {}
        self.capture_count = 0
        self.max_capture = 100
        self.error = None
        self.duplicate_hits = 0
        self.matched_student = None
        if not os.path.exists('dataset'):
            os.makedirs('dataset')
            print("[SYSTEM] Created missing dataset directory.")

    def reset(self, mode='idle'):
        self.mode = mode
        self.capture_count = 0
        self.error = None
        self.duplicate_hits = 0
        self.matched_student = None
        self.active_params = {}
        print(f"[APP_STATE] Reset to {mode} mode.")

class CameraManager:
    def __init__(self):
        self.cap = None
        self.is_running = False
        print("[CAMERA] Manager Initialized.")

    def start(self):
        # If already open AND actually delivering frames, do nothing
        if self.cap is not None and self.cap.isOpened():
            ret, f = self.cap.read()
            if ret and f is not None:
                self.is_running = True
                return  # Camera is healthy, no action needed
            # Cap object exists but isn't reading — force a clean restart
            print("[CAMERA] Stale handle detected, forcing reopen...")
            self.cap.release()
            self.cap = None
            self.is_running = False
        
        print("[CAMERA] Opening sensor...")
        for idx in [0, 1, 2]:
            for backend in [cv2.CAP_DSHOW, None]:
                try:
                    c = cv2.VideoCapture(idx, backend) if backend else cv2.VideoCapture(idx)
                    if c.isOpened():
                        # Verify it actually delivers a frame before accepting
                        ret, f = c.read()
                        if ret and f is not None:
                            self.cap = c
                            self.is_running = True
                            print(f"[CAMERA] Sensor {idx} active (backend={backend}).")
                            return
                        c.release()
                except Exception as e:
                    print(f"[CAMERA] Index {idx} failed: {e}")
        print("[CAMERA] ERROR: No working sensor found.")

    def get_frame(self):
        if not self.is_running or self.cap is None:
            return None
        # Retry up to 3 times on transient read failures
        for _ in range(3):
            ret, frame = self.cap.read()
            if ret and frame is not None:
                return frame
        # If all retries fail, flag camera as not running so start() will reopen it
        print("[CAMERA] Frame read failed 3x — marking as not running.")
        self.is_running = False
        return None

    def stop(self):
        self.is_running = False
        if self.cap:
            self.cap.release()
        self.cap = None
        print("[CAMERA] Stopped.")

    def restart(self):
        self.stop()
        time.sleep(0.8)  # Give OS time to release the device
        self.start()

# Initialization 
app_state = AppState()
camera = CameraManager()
_browser_training_locks = set()
_browser_duplicate_hits = {}
_dup_model_cache = {
    'recognizer': None,
    'label_map': {},
    'file_count': -1,
    'built_at': 0.0
}


def _norm_token(v):
    return ''.join(ch for ch in str(v).lower() if ch.isalnum())


def _acr(v):
    parts = [p for p in str(v).replace('-', ' ').replace('_', ' ').split() if p]
    return ''.join(p[0].lower() for p in parts)


def _class_matches_student(selected_class_id, student):
    if not student:
        return False

    selected_class_id = str(selected_class_id).strip()
    student_class_raw = str(student.get('ClassId', '')).strip()

    cls = db._cache['classes'].get(selected_class_id, {})
    selected_short = str(cls.get('ShortName', '')).strip().lower()
    selected_name = str(cls.get('ClassName', '')).strip().lower()
    student_class_norm = student_class_raw.lower()

    selected_id_norm = _norm_token(selected_class_id)
    selected_short_norm = _norm_token(selected_short)
    selected_name_norm = _norm_token(selected_name)
    student_class_id_norm = _norm_token(student_class_raw)
    student_class_acr = _acr(student_class_raw)
    selected_name_acr = _acr(selected_name)

    return (
        student_class_raw == selected_class_id or
        (selected_short and student_class_norm == selected_short) or
        (selected_name and student_class_norm == selected_name) or
        (selected_short_norm and (student_class_id_norm == selected_short_norm or selected_short_norm in student_class_id_norm)) or
        (selected_name_norm and (student_class_id_norm == selected_name_norm or selected_name_norm in student_class_id_norm)) or
        (selected_short_norm and student_class_acr and selected_short_norm == student_class_acr) or
        (selected_name_acr and student_class_acr and selected_name_acr == student_class_acr) or
        (selected_id_norm and student_class_id_norm == selected_id_norm)
    )


def _decode_data_url_to_frame(data_url):
    if not data_url:
        return None
    try:
        encoded = data_url.split(',', 1)[1] if ',' in data_url else data_url
        img_bytes = base64.b64decode(encoded)
        npbuf = np.frombuffer(img_bytes, dtype=np.uint8)
        frame = cv2.imdecode(npbuf, cv2.IMREAD_COLOR)
        return frame
    except Exception:
        return None


def _preprocess_face_gray(face_gray):
    face_resized = cv2.resize(face_gray, (200, 200))
    face_equalized = cv2.equalizeHist(face_resized)
    kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])
    return cv2.filter2D(face_equalized, -1, kernel)


def _count_dataset_images(dataset_root='dataset'):
    total = 0
    if not os.path.isdir(dataset_root):
        return 0
    for root, _, files in os.walk(dataset_root):
        total += sum(1 for f in files if f.lower().endswith('.jpg'))
    return total


def _get_duplicate_guard_model():
    dataset_root = 'dataset'
    if not os.path.isdir(dataset_root):
        return None, {}

    file_count = _count_dataset_images(dataset_root)
    now = time.time()
    if (
        _dup_model_cache['recognizer'] is not None and
        _dup_model_cache['file_count'] == file_count and
        (now - _dup_model_cache['built_at']) < 30
    ):
        return _dup_model_cache['recognizer'], _dup_model_cache['label_map']

    faces = []
    labels = []
    label_map = {}
    label_idx = 1

    for class_dir in os.listdir(dataset_root):
        class_path = os.path.join(dataset_root, class_dir)
        if not os.path.isdir(class_path):
            continue
        for student_token in os.listdir(class_path):
            student_path = os.path.join(class_path, student_token)
            if not os.path.isdir(student_path):
                continue

            current_label = label_idx
            label_map[current_label] = str(student_token)
            label_idx += 1

            for fname in os.listdir(student_path):
                if not fname.lower().endswith('.jpg'):
                    continue
                img = cv2.imread(os.path.join(student_path, fname), cv2.IMREAD_GRAYSCALE)
                if img is None:
                    continue
                faces.append(_preprocess_face_gray(img))
                labels.append(current_label)

    if len(faces) < 10:
        _dup_model_cache['recognizer'] = None
        _dup_model_cache['label_map'] = {}
        _dup_model_cache['file_count'] = file_count
        _dup_model_cache['built_at'] = now
        return None, {}

    recog = cv2.face.LBPHFaceRecognizer_create(radius=1, neighbors=8, grid_x=10, grid_y=10, threshold=120.0)
    recog.train(faces, np.array(labels))

    _dup_model_cache['recognizer'] = recog
    _dup_model_cache['label_map'] = label_map
    _dup_model_cache['file_count'] = file_count
    _dup_model_cache['built_at'] = now
    return recog, label_map


@app.route('/api/runtime_config')
def api_runtime_config():
    return jsonify({
        'camera_mode': CAMERA_MODE,
        'server_streaming_supported': CAMERA_MODE == 'server'
    })


@app.route('/api/recognize_frame', methods=['POST'])
def api_recognize_frame():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    payload = request.json or {}
    frame = _decode_data_url_to_frame(payload.get('image'))
    if frame is None:
        return jsonify({'success': False, 'message': 'Invalid frame payload'}), 400

    class_id = payload.get('class_id')
    subject_id = payload.get('subject_id')
    section = payload.get('section', 'A')
    date_str = payload.get('date')

    if not class_id or not subject_id:
        return jsonify({'success': False, 'message': 'class_id and subject_id are required'}), 400

    if not engine.model_loaded:
        return jsonify({'success': True, 'detections': [], 'marked': [], 'message': 'Model not loaded'})

    teacher_id = session.get('user_id', 1)
    expected_sid = session.get('user_id') if session.get('role') == 'student' else None

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray_eq = cv2.equalizeHist(gray)
    faces = engine.face_cascade.detectMultiScale(gray_eq, 1.1, 6, minSize=(60, 60))

    detections = []
    marked = []
    recon_threshold = 115
    strict_mark_threshold = 95

    for (x, y, w, h) in faces:
        roi = gray_eq[y:y+h, x:x+w]
        if roi.size == 0:
            continue
        roi_resized = cv2.resize(roi, (200, 200))
        kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])
        roi_final = cv2.filter2D(roi_resized, -1, kernel)

        try:
            label, conf = engine.recognizer.predict(roi_final)
        except Exception:
            continue

        if conf >= recon_threshold:
            continue

        student = db.get_student_by_id(str(label))
        if not student:
            continue

        real_id = str(student.get('id') or label)
        is_authorized = not expected_sid or str(real_id) == str(expected_sid)
        class_match = _class_matches_student(class_id, student)
        sec_match = True if not section else str(student.get('Section', '')) == str(section)

        status = 'scanning'
        if not is_authorized:
            status = 'unauthorized'
        elif not class_match:
            status = 'wrong_class'
        elif not sec_match:
            status = 'wrong_section'
        else:
            status = 'verified'
            if conf < strict_mark_threshold:
                if db.log_attendance(real_id, class_id, teacher_id, subject_id, section, date_str):
                    marked.append(real_id)

        detections.append({
            'student_id': real_id,
            'name': student.get('FullName', 'Student'),
            'confidence': float(conf),
            'status': status
        })

    return jsonify({'success': True, 'detections': detections, 'marked': marked})


@app.route('/api/capture_frame', methods=['POST'])
def api_capture_frame():
    if session.get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    payload = request.json or {}
    student_id = str(payload.get('student_id', '')).strip()
    class_id = str(payload.get('class_id', '')).strip()
    if not student_id or not class_id:
        return jsonify({'success': False, 'message': 'student_id and class_id are required'}), 400

    frame = _decode_data_url_to_frame(payload.get('image'))
    if frame is None:
        return jsonify({'success': False, 'message': 'Invalid frame payload'}), 400

    target_dir = os.path.join('dataset', class_id, student_id)
    os.makedirs(target_dir, exist_ok=True)
    existing = [f for f in os.listdir(target_dir) if f.lower().endswith('.jpg')]
    count = len(existing)
    max_capture = 100
    dup_key = f"{class_id}:{student_id}"

    if count >= max_capture:
        _browser_duplicate_hits.pop(dup_key, None)
        lock_key = f"{class_id}:{student_id}"
        if lock_key not in _browser_training_locks:
            _browser_training_locks.add(lock_key)

            def _train_and_unlock():
                try:
                    engine.train_model_v2(db)
                finally:
                    _browser_training_locks.discard(lock_key)

            threading.Thread(target=_train_and_unlock, daemon=True).start()

        return jsonify({'success': True, 'count': max_capture, 'progress': 100, 'done': True})

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray_eq = cv2.equalizeHist(gray)
    faces = engine.face_cascade.detectMultiScale(gray_eq, scaleFactor=1.08, minNeighbors=4, minSize=(80, 80))

    if len(faces) == 0:
        progress = int((count / max_capture) * 100)
        return jsonify({'success': True, 'count': count, 'progress': progress, 'done': False})

    x, y, w, h = faces[0]
    face_roi = gray_eq[y:y+h, x:x+w]
    if face_roi.size == 0:
        progress = int((count / max_capture) * 100)
        return jsonify({'success': True, 'count': count, 'progress': progress, 'done': False})

    # Strong duplicate guard backed by current dataset so stale model state cannot bypass checks.
    face_proc = _preprocess_face_gray(face_roi)
    dup_recog, label_map = _get_duplicate_guard_model()
    duplicate_sid = None
    duplicate_conf = 999.0

    if dup_recog is not None:
        try:
            pred_label, pred_conf = dup_recog.predict(face_proc)
            candidate_sid = str(label_map.get(pred_label, ''))
            # Lower confidence is better in LBPH; tuned higher for stricter duplicate blocking.
            if candidate_sid and candidate_sid != str(student_id) and pred_conf < 88:
                duplicate_sid = candidate_sid
                duplicate_conf = float(pred_conf)
        except Exception:
            pass

    # Fallback to engine model if dataset-guard could not decide.
    if duplicate_sid is None and engine.model_loaded:
        is_dup, matched_id, conf = engine.check_duplicate(face_roi, threshold=78)
        if is_dup and str(matched_id) != str(student_id):
            duplicate_sid = str(matched_id)
            duplicate_conf = float(conf)

    if duplicate_sid is not None:
        hits = _browser_duplicate_hits.get(dup_key, 0) + 1
        _browser_duplicate_hits[dup_key] = hits
        if hits >= 3:
            matched_student = db.get_student_by_id(str(duplicate_sid)) or {}
            return jsonify({
                'success': False,
                'duplicate': True,
                'message': 'student already exist',
                'matched_student': {
                    'id': str(matched_student.get('id', duplicate_sid)),
                    'name': matched_student.get('FullName', 'Unknown'),
                    'enrollment_no': matched_student.get('EnrollmentNo', 'N/A')
                },
                'confidence': duplicate_conf
            }), 409
    else:
        _browser_duplicate_hits[dup_key] = 0

    next_count = count + 1
    save_path = os.path.join(target_dir, f"{next_count}.jpg")
    cv2.imwrite(save_path, cv2.resize(face_roi, (200, 200)))
    progress = int((next_count / max_capture) * 100)

    if next_count >= max_capture:
        _browser_duplicate_hits.pop(dup_key, None)
        lock_key = f"{class_id}:{student_id}"
        if lock_key not in _browser_training_locks:
            _browser_training_locks.add(lock_key)

            def _train_and_unlock_done():
                try:
                    engine.train_model_v2(db)
                finally:
                    _browser_training_locks.discard(lock_key)

            threading.Thread(target=_train_and_unlock_done, daemon=True).start()

    return jsonify({'success': True, 'count': next_count, 'progress': progress, 'done': next_count >= max_capture})

@app.route('/')
def login_page():
    return render_template('login.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    print(f"[AUTH] {data.get('username')} attempting to login as {data.get('role')}...")
    user = db.get_user(data['username'], data['password'], role=data.get('role'))
    if user:
        print(f"[AUTH] SUCCESS: Logged in as {user.get('Role')}")
        session['user_id'] = user['id']
        session['role'] = user['Role']
        # Handle 'Name' (Teacher) or 'FullName' (Student)
        display_name = user.get('Name') or user.get('FullName') or user.get('Username')
        session['name'] = display_name
        
        return jsonify({
            'success': True, 
            'role': user['Role'],
            'redirect': '/app',
            'user': {
                'id': user['id'],
                'role': user['Role'],
                'name': display_name
            }
        })
    print(f"[AUTH] FAILED: Invalid credentials for {data.get('username')}")
    return jsonify({'success': False, 'message': 'Invalid Credentials'}), 401

@app.route('/app')
def main_app():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('index.html', user=session)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

@app.route('/register', methods=['GET', 'POST'])
def view_register():
    if session.get('role') != 'admin':
        if request.is_json:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        return redirect(url_for('login_page'))
    if request.method == 'POST':
        try:
            data = request.json
            print(f"[DEBUG] Registration Request: {data.get('full_name')} ({data.get('enrollment_no')})")
            sid = db.add_student(
                full_name=data['full_name'],
                class_id=data['class_id'],
                enrollment_no=data['enrollment_no'],
                username=data['username'],
                password=data['password'],
                phone=data.get('phone', ''),
                email=data.get('email', ''),
                dob=data.get('dob'),
                address=data.get('address', '')
            )
            print(f"[DEBUG] Registration Success: Generated ID {sid}")
            return jsonify({'success': True, 'id': sid})
        except Exception as e:
            print(f"[DEBUG] Registration FAILED: {str(e)}")
            return jsonify({'success': False, 'message': str(e)}), 500
            
    classes = db.get_classes(active_only=True)
    print(f"[DEBUG] Fetched {len(classes)} active classes for registration page.")
    return render_template('register_page.html', classes=classes)

@app.route('/api/whoami')
def who_am_i():
    if 'user_id' not in session: return jsonify({'role': 'guest'}), 401
    return jsonify({
        'id': session.get('user_id'),
        'role': session.get('role'),
        'name': session.get('name')
    })

@app.route('/api/stats')
def api_stats():
    return jsonify(db.get_stats())

@app.route('/api/dept_stats')
def api_dept_stats():
    """Returns student count grouped by department for the admin dashboard."""
    from collections import defaultdict
    students = db.get_all_students()
    classes = {c['id']: c for c in db.get_classes(active_only=False)}
    
    dept_counts = defaultdict(lambda: {'name': 'Unknown', 'short': 'N/A', 'total': 0, 'active': 0})
    for s in students:
        cid = s.get('ClassId', '')
        dept_counts[cid]['total'] += 1
        if s.get('IsActive'):
            dept_counts[cid]['active'] += 1
        if cid in classes:
            dept_counts[cid]['name'] = classes[cid].get('ClassName', 'Unknown')
            dept_counts[cid]['short'] = classes[cid].get('ShortName', 'N/A')
        else:
            dept_counts[cid]['name'] = s.get('ClassName', 'Unknown')
            dept_counts[cid]['short'] = s.get('ClassId', 'N/A')
    
    result = [{'id': k, **v} for k, v in dept_counts.items()]
    result.sort(key=lambda x: x['total'], reverse=True)
    return jsonify(result)

@app.route('/api/classes', methods=['GET', 'POST'])
def api_classes():
    if request.method == 'POST':
        if session.get('role') != 'admin':
            return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        data = request.json
        db.add_class(data['name'], data['short_name'])
        return jsonify({'success': True})
    return jsonify(db.get_classes())

@app.route('/api/teachers', methods=['GET', 'POST'])
def api_teachers():
    if session.get('role') != 'admin': return jsonify([]), 401
    
    if request.method == 'POST':
        data = request.json
        email = str(data.get('email', '')).strip().lower()
        phone = str(data.get('phone', '')).strip().replace(' ', '')

        if not re.match(r'^[A-Za-z0-9._%+-]+@gmail\.com$', email):
            return jsonify({'success': False, 'message': 'Email must be a valid @gmail.com address'}), 400
        if not re.match(r'^\+91\d{10}$', phone):
            return jsonify({'success': False, 'message': 'Contact number must be in +91XXXXXXXXXX format'}), 400

        success = db.add_teacher(
            name=data['name'],
            username=data['username'],
            password=data['password'],
            specialization=data['specialization'],
            subject_ids=data.get('subject_ids', []),
            faculty_id=data.get('faculty_id'),
            department=data.get('department'),
            email=email,
            phone=phone
        )
        return jsonify({'success': success})
        
    return jsonify(db.get_teachers())

@app.route('/api/assignments')
def api_assignments():
    return jsonify(db.get_all_assignments())

@app.route('/api/faculty_db')
def api_faculty_db():
    if session.get('role') != 'admin':
        return jsonify([]), 401
    return jsonify(db.get_faculty_db(active_only=False))

@app.route('/api/assign', methods=['POST'])
def api_assign():
    if session.get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    data = request.json
    db.assign_teacher(data['teacher_id'], data['class_id'], data['subject_id'], session.get('name', 'Admin'))
    return jsonify({'success': True})

@app.route('/api/student/summary')
def api_student_summary():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(db.get_student_summary(session['user_id']))

@app.route('/api/student/attendance')
def api_student_attendance():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(db.get_student_attendance(session['user_id']))

@app.route('/api/student/attendance/date')
def api_student_attendance_date():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    date_str = request.args.get('date')
    if not date_str: return jsonify([])
    return jsonify(db.get_student_attendance_by_date(session['user_id'], date_str))

@app.route('/api/student/attendance/day')
def api_student_attendance_day():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    day_name = request.args.get('day')
    if not day_name: return jsonify([])
    return jsonify(db.get_student_attendance_by_day(session['user_id'], day_name))

@app.route('/api/student/cumulative')
def api_student_cumulative():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(db.get_cumulative_attendance(session['user_id']))

@app.route('/api/student/photo/<class_id>/<enroll_no>')
def api_student_photo(class_id, enroll_no):
    # Safety: Ensure no path traversal
    if '..' in class_id or '..' in enroll_no: return "Invalid Path", 400
    path = os.path.join('dataset', class_id, enroll_no)
    return send_from_directory(path, '1.jpg')

@app.route('/api/student/profile')
def api_student_profile():
    curr_role = str(session.get('role', '')).lower()
    if 'user_id' not in session:
        return jsonify({'error': 'Session Missing: Please Login Again'}), 401
    if curr_role != 'student':
        return jsonify({'error': f'Role Mismatch: Found {curr_role}'}), 401
    
    student = db.get_student_by_id(session['user_id'])
    if not student:
        return jsonify({'error': 'Student not found'}), 404
        
    # Generate Photo URL
    if student.get('ClassId') and student.get('EnrollmentNo'):
        student['photo_url'] = f"/api/student/photo/{student['ClassId']}/{student['EnrollmentNo']}"
        
    return jsonify(student)

@app.route('/api/assigned_classes')
def api_assigned_classes():
    role = session.get('role')
    user_id = session.get('user_id')
    
    if role == 'faculty' and user_id:
        # Get real assignments for this faculty
        return jsonify(db.get_teacher_assignments(user_id))
    
    # Fallback/Admin View: Keep hardcoded courses for now or return all classes
    return jsonify([
        {'id': 'Yl82wM0a9SjtpAOybLWF', 'ClassName': 'Bachelor of Computer Applications', 'ShortName': 'BCA'},
        {'id': 'GAdSmkmohSDeto9rpuhb', 'ClassName': 'Master of Computer Applications', 'ShortName': 'MCA'},
        {'id': 'ZtQbGqNEp0efp6ZcyN7G', 'ClassName': 'Bachelor of Technology', 'ShortName': 'BTech'},
        {'id': 'CKTNvGp3xqQKV3oWPFKG', 'ClassName': 'Master of Technology', 'ShortName': 'MTech'}
    ])

@app.route('/api/subjects', methods=['GET', 'POST'])
def api_subjects():
    if request.method == 'POST':
        if session.get('role') != 'admin':
            return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        data = request.json
        db.add_subject(data['name'], data['code'], data.get('class_id'))
        return jsonify({'success': True})

    class_id = request.args.get('class_id')
    subjects = db.get_subjects_by_class(class_id)
    return jsonify(subjects)

@app.route('/api/sections')
def api_sections():
    class_id = request.args.get('class_id')
    sections = db.get_sections_by_class(class_id)
    print(f"[DEBUG] Fetched {len(sections)} sections for class_id: {class_id}")
    return jsonify(sections)

@app.route('/api/students/attendance_list')
def api_attendance_list():
    if 'user_id' not in session: return jsonify([]), 401
    class_id = request.args.get('class_id')
    section = request.args.get('section')
    
    # Faculty security check
    if session.get('role') == 'faculty':
        assigned = db.get_teacher_assignments(session.get('user_id'))
        if not any(str(a.get('class_id')) == str(class_id) for a in assigned):
            return jsonify({'error': 'Unauthorized class access'}), 403
            
    return jsonify(db.get_students_by_filter(class_id, section))

@app.route('/api/register', methods=['POST'])
def api_register():
    if session.get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    try:
        data = request.json
        if not data: return jsonify({'success': False, 'message': 'Missing fields'}), 400
        
        sid = db.add_student(
            full_name=data['name'], 
            class_id=data['class_id'], 
            enrollment_no=data['enrollment_no'], 
            username=data['username'], 
            password=data['password'],
            section=data.get('section', 'A'),
            batch=data.get('batch', '2024')
        )
        if sid:
            return jsonify({'success': True, 'id': sid})
        return jsonify({'success': False, 'message': 'Registration returned NO student ID'}), 500
    except Exception as e:
        print(f"[REGISTER ERROR] {str(e)}")
        return jsonify({'success': False, 'message': f'Server Error: {str(e)}'}), 500

@app.route('/api/students')
def api_students():
    if session.get('role') != 'admin' and session.get('role') != 'faculty':
        return jsonify([]), 401
    class_id = request.args.get('class_id')
    res = db.get_students_by_class(class_id) if class_id else db.get_all_students()
    return jsonify(res)

@app.route('/api/students/status', methods=['POST'])
def api_student_status():
    if session.get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    data = request.json
    success = db.soft_delete_student(data['id'], data['active'])
    return jsonify({'success': success})

@app.route('/api/reports')
def api_reports():
    return jsonify(db.get_all_reports())

@app.route('/get_all_attendance')
def get_all_attendance():
    return jsonify(db.get_all_reports())

@app.route('/api/attendance_log')
def api_attendance_log():
    if 'user_id' not in session: return jsonify([]), 401
    class_id = request.args.get('class_id')
    subject_id = request.args.get('subject_id')
    section = request.args.get('section')
    date_str = request.args.get('date')
    
    # Faculty security check
    if session.get('role') == 'faculty':
        assigned = db.get_teacher_assignments(session.get('user_id'))
        if not any(str(a.get('class_id')) == str(class_id) for a in assigned):
            return jsonify({'error': 'Unauthorized class access'}), 403
            
    return jsonify(db.get_attendance_today(class_id, subject_id, section, date_str))

@app.route('/api/attendance/manual', methods=['POST'])
def api_attendance_manual():
    try:
        data = request.json
        success = db.log_attendance(
            sid=data['student_id'],
            cid=data['class_id'],
            tid=session.get('user_id', 1),
            sub_id=data['subject_id'],
            section=data.get('section', 'A'),
            date_str=data.get('date')
        )
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

def generate_frames(teacher_id=1, expected_sid=None):
    hits_dict = {}
    logged_once = set()  # Session-level guard: avoid repeated write attempts for same student
    student_cache = {}  # In-memory cache: {str(id): student_dict} — avoids Firebase reads every frame
    mismatch_debug_hits = {}
    class_model_cache = {}  # {class_id: LBPH recognizer or None}
    recon_threshold = 115 # More tolerant for real-world webcam variance; class check still protects marking
    strict_mark_threshold = 95  # Only mark attendance for reasonably strong matches
    mismatch_alert_threshold = 55  # Only show NOT IN CLASS for very strong wrong-class matches
    _camera_retry = 0   # Tracks consecutive frame failures for self-healing

    def _build_class_recognizer(selected_class_id):
        """Train a lightweight LBPH model from only the selected class dataset."""
        class_dir = os.path.join('dataset', str(selected_class_id))
        if not os.path.isdir(class_dir):
            return None

        faces = []
        labels = []
        for token in os.listdir(class_dir):
            token_dir = os.path.join(class_dir, token)
            if not os.path.isdir(token_dir):
                continue

            student = None
            if str(token).isdigit():
                s_by_id = db.get_student_by_id(token)
                if s_by_id and str(s_by_id.get('ClassId')) == str(selected_class_id):
                    student = s_by_id
            if not student:
                student = db.get_student_by_enrollment(token, class_id=selected_class_id)
            if not student:
                continue

            sid = int(student.get('id'))
            for filename in os.listdir(token_dir):
                if not filename.lower().endswith('.jpg'):
                    continue
                img = cv2.imread(os.path.join(token_dir, filename), cv2.IMREAD_GRAYSCALE)
                if img is None:
                    continue
                img_resized = cv2.resize(img, (200, 200))
                img_equalized = cv2.equalizeHist(img_resized)
                kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])
                img_sharp = cv2.filter2D(img_equalized, -1, kernel)
                faces.append(img_sharp)
                labels.append(sid)

        if not faces:
            return None

        recog = cv2.face.LBPHFaceRecognizer_create(radius=1, neighbors=8, grid_x=10, grid_y=10, threshold=100.0)
        recog.train(faces, np.array(labels))
        print(f"[RECOG] Class model ready for {selected_class_id} with {len(faces)} samples.")
        return recog

    while True:
        try:
            frame_orig = camera.get_frame()
        except Exception as e:
            print(f"[CRITICAL] Camera sensor error: {e}")
            frame_orig = None

        if frame_orig is None:
            _camera_retry += 1
            # Self-healing: try to (re)open the camera after 5 consecutive missed frames
            if _camera_retry % 5 == 1:
                print(f"[CAMERA] Frame miss #{_camera_retry} — attempting self-heal...")
                camera.start()
                time.sleep(0.4)
                # Try to get a frame immediately after restart
                frame_orig = camera.get_frame()

        if frame_orig is None:
            # Create a black frame with 'CAMERA OFFLINE' if sensor fails
            error_frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(error_frame, "HARDWARE SENSOR OFFLINE", (100, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            cv2.putText(error_frame, "Click 'Fix Camera' to reboot", (150, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)
            ret, buffer = cv2.imencode('.jpg', error_frame)
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
            time.sleep(1.0)
            continue
            
        frame = frame_orig.copy()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_eq = cv2.equalizeHist(gray)
        
        try:
            if app_state.mode == 'capture':
                p = app_state.active_params
                if not p or 'student_id' not in p or 'class_id' not in p:
                     cv2.putText(frame, "INIT ERROR: NO PARAMS", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                else:
                    # Increased sensitivity for face detection
                    faces = engine.face_cascade.detectMultiScale(
                        gray_eq, 
                        scaleFactor=1.08, # Slightly more sensitive than 1.1
                        minNeighbors=4,  # Lowered from 5
                        minSize=(80, 80) # Lowered from 100x100 for better distance support
                    )
                    
                    if len(faces) == 0:
                        cv2.putText(frame, "CENTER YOUR FACE", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 165, 255), 2)
                        h, w = frame.shape[:2]
                        cx, cy = w//2, h//2
                        cv2.rectangle(frame, (cx-100, cy-100), (cx+100, cy+100), (0, 165, 255), 1, cv2.LINE_AA)
                    elif app_state.error:
                        color = (0, 0, 255)
                        msg = "ERROR: FACE ALREADY REGISTERED" if app_state.error == 'DUPLICATE' else app_state.error
                        cv2.putText(frame, msg, (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                        # Draw X over face
                        for (x, y, w, h) in faces:
                            cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)
                            cv2.line(frame, (x, y), (x+w, y+h), color, 2)
                            cv2.line(frame, (x+w, y), (x, y+h), color, 2)
                    else:
                        cv2.putText(frame, f"Detecting: {len(faces)} face(s)", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (34, 197, 94), 2)
                    
                    for (x, y, w, h) in faces:
                        if app_state.error: continue
                        
                        # NOTE: LBPH-based duplicate check removed.
                        # LBPH always predicts the nearest label even for completely unknown faces,
                        # so it produces false positives ("already registered") for every new person
                        # when the training dataset is small. Duplicate prevention is enforced
                        # correctly at the database level via unique Enrollment IDs.

                        if app_state.capture_count < app_state.max_capture:
                            app_state.capture_count += 1
                            class_dir = str(p['class_id'])
                            # Use student_id as canonical dataset token to avoid collisions
                            # when two students share the same enrollment number.
                            student_dir = str(p.get('student_id', p.get('enrollment_no')))
                            target_dir = os.path.join('dataset', class_dir, student_dir)
                            if not os.path.exists(target_dir): os.makedirs(target_dir)
                                
                            save_path = os.path.join(target_dir, f"{app_state.capture_count}.jpg")
                            face_roi = gray_eq[y:y+h, x:x+w]
                            cv2.imwrite(save_path, cv2.resize(face_roi, (200, 200)))
                            
                            cv2.rectangle(frame, (x, y), (x+w, y+h), (56, 189, 248), 2)
                            percent = int((app_state.capture_count / app_state.max_capture) * 100)
                            cv2.putText(frame, f"Capturing: {percent}%", (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (56, 189, 248), 2)
                        else:
                            app_state.mode = 'idle'
                            threading.Thread(target=engine.train_model_v2, args=(db,), daemon=True).start()
                            print(f"[REGISTRATION] Biometrics saved for {p.get('enrollment_no')}")
            
            elif app_state.mode == 'recognize':
                p = app_state.active_params or {}
                # Crucial Fix: Use safe .get() to prevent crashes if UI state is incomplete
                class_id = p.get('class_id')
                subject_id = p.get('subject_id')
                
                if not class_id or not subject_id:
                     cv2.putText(frame, "SELECT CLASS & SUBJECT", (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                else:
                    faces = engine.face_cascade.detectMultiScale(gray_eq, 1.1, 6, minSize=(60, 60))
                    for (x, y, w, h) in faces:
                        class_key = str(class_id).strip()
                        if class_key not in class_model_cache:
                            class_model_cache[class_key] = _build_class_recognizer(class_key)
                        predictor = class_model_cache.get(class_key) or (engine.recognizer if engine.model_loaded else None)

                        if predictor is None:
                            cv2.putText(frame, "MODEL NOT LOADED", (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)
                            continue
                            
                        try:
                            roi = gray_eq[y:y+h, x:x+w]
                            if roi.size == 0: continue
                            roi_resized = cv2.resize(roi, (200, 200))
                            # Sharpen for LBPH consistency
                            kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
                            roi_final = cv2.filter2D(roi_resized, -1, kernel)
                            
                            lbl, conf = predictor.predict(roi_final)
                            
                            # Recognition Logic with Confidence Check
                            if conf < recon_threshold: 
                                real_id = str(lbl)
                                # Use cache to avoid Firestore read every frame
                                if real_id not in student_cache:
                                    student_cache[real_id] = db.get_student_by_id(real_id)
                                student = student_cache[real_id]
                                current_section = p.get('section', 'A')
                                
                                # Authorization: Session Matching (Optional)
                                is_authorized = True
                                if expected_sid and str(real_id) != str(expected_sid):
                                    is_authorized = False
                                
                                if not is_authorized:
                                    color = (0, 0, 255); label_txt = "UNAUTHORIZED ACCESS"
                                elif student:
                                    # Robust class match: supports both modern class doc IDs and legacy class values.
                                    selected_class_id = str(class_id).strip()
                                    student_class_raw = str(student.get('ClassId', '')).strip()

                                    cls = db._cache['classes'].get(selected_class_id, {})
                                    selected_short = str(cls.get('ShortName', '')).strip().lower()
                                    selected_name = str(cls.get('ClassName', '')).strip().lower()
                                    student_class_norm = student_class_raw.lower()

                                    # Normalize to alphanumeric tokens so legacy values like
                                    # "Bachelor of Computer Applications" and "BCA" can be
                                    # compared safely against id/name/short-name variants.
                                    def _norm(v):
                                        return ''.join(ch for ch in str(v).lower() if ch.isalnum())

                                    def _acr(v):
                                        parts = [p for p in str(v).replace('-', ' ').replace('_', ' ').split() if p]
                                        return ''.join(p[0].lower() for p in parts)

                                    def _sing(v):
                                        t = str(v)
                                        return t[:-1] if t.endswith('s') else t

                                    selected_id_norm = _norm(selected_class_id)
                                    selected_short_norm = _norm(selected_short)
                                    selected_name_norm = _norm(selected_name)
                                    student_class_id_norm = _norm(student_class_raw)
                                    student_class_acr = _acr(student_class_raw)
                                    selected_name_acr = _acr(selected_name)
                                    selected_name_sing = _sing(selected_name_norm)
                                    student_class_sing = _sing(student_class_id_norm)

                                    class_match = (
                                        student_class_raw == selected_class_id or
                                        (selected_short and student_class_norm == selected_short) or
                                        (selected_name and student_class_norm == selected_name) or
                                        (selected_short_norm and (student_class_id_norm == selected_short_norm or selected_short_norm in student_class_id_norm)) or
                                        (selected_name_norm and (student_class_id_norm == selected_name_norm or selected_name_norm in student_class_id_norm)) or
                                        (selected_name_sing and student_class_sing and (selected_name_sing == student_class_sing or selected_name_sing in student_class_sing or student_class_sing in selected_name_sing)) or
                                        (selected_short_norm and student_class_acr and selected_short_norm == student_class_acr) or
                                        (selected_name_acr and student_class_acr and selected_name_acr == student_class_acr) or
                                        (selected_id_norm and student_class_id_norm == selected_id_norm)
                                    )

                                    # If enrollment numbers are reused across classes, LBPH can predict
                                    # the wrong sibling id. Remap to the selected class student using
                                    # enrollment as tie-breaker before declaring class mismatch.
                                    if not class_match:
                                        predicted_enroll = str(student.get('EnrollmentNo', '')).strip()
                                        if predicted_enroll:
                                            remapped = db.get_student_by_enrollment(predicted_enroll, class_id=selected_class_id)
                                            if remapped and str(remapped.get('id')) != str(real_id):
                                                real_id = str(remapped.get('id'))
                                                student = remapped
                                                student_cache[real_id] = remapped
                                                student_class_raw = str(student.get('ClassId', '')).strip()
                                                student_class_norm = student_class_raw.lower()
                                                student_class_id_norm = _norm(student_class_raw)
                                                student_class_acr = _acr(student_class_raw)
                                                student_class_sing = _sing(student_class_id_norm)
                                                class_match = (
                                                    student_class_raw == selected_class_id or
                                                    (selected_short and student_class_norm == selected_short) or
                                                    (selected_name and student_class_norm == selected_name) or
                                                    (selected_short_norm and (student_class_id_norm == selected_short_norm or selected_short_norm in student_class_id_norm)) or
                                                    (selected_name_norm and (student_class_id_norm == selected_name_norm or selected_name_norm in student_class_id_norm)) or
                                                    (selected_name_sing and student_class_sing and (selected_name_sing == student_class_sing or selected_name_sing in student_class_sing or student_class_sing in selected_name_sing)) or
                                                    (selected_short_norm and student_class_acr and selected_short_norm == student_class_acr) or
                                                    (selected_name_acr and student_class_acr and selected_name_acr == student_class_acr) or
                                                    (selected_id_norm and student_class_id_norm == selected_id_norm)
                                                )

                                    if class_match:
                                        sec_match = True
                                        if current_section and current_section != "":
                                            sec_match = str(student.get('Section')) == str(current_section)

                                        if sec_match:
                                            hits_dict[real_id] = hits_dict.get(real_id, 0) + 1
                                            # Write once per session as soon as identity/class/section checks pass.
                                            if real_id not in logged_once and conf < strict_mark_threshold:
                                                db.log_attendance(real_id, class_id, teacher_id, subject_id, current_section, p.get('date'))
                                                logged_once.add(real_id)

                                            color = (34, 197, 94) # Green
                                            name = student.get('FullName', 'Student')
                                            label_txt = f"{name} Verified"
                                        else:
                                            color = (239, 68, 68); label_txt = "WRONG SECTION"
                                    else:
                                        miss_key = f"{real_id}:{selected_class_id}"
                                        mismatch_debug_hits[miss_key] = mismatch_debug_hits.get(miss_key, 0) + 1
                                        if mismatch_debug_hits[miss_key] % 20 == 1:
                                            print(
                                                f"[RECOG-MISMATCH] pred_sid={real_id} conf={conf:.2f} "
                                                f"student_class='{student_class_raw}' selected_class_id='{selected_class_id}' "
                                                f"selected_short='{selected_short}' selected_name='{selected_name}'"
                                            )
                                        # Keep mismatch feedback non-blocking to avoid false negatives.
                                        color = (36, 150, 255); label_txt = "Scanning..."
                                else:
                                    color = (36, 150, 255); label_txt = "Scanning..."
                            else:
                                color = (36, 150, 255); label_txt = "Scanning..." # Blue
                                
                            cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)
                            cv2.putText(frame, label_txt, (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

                            # Lightweight diagnostic overlay to help resolve persistent class mismatch.
                            if app_state.mode == 'recognize' and conf < recon_threshold:
                                dbg_line = f"sid:{real_id} conf:{conf:.1f} cls:{student.get('ClassId','?') if student else '?'} sel:{class_id}"
                                cv2.putText(frame, dbg_line[:90], (10, frame.shape[0]-12), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
                        except Exception as inner_e:
                            print(f"[INNER_RECOG_ERROR] {inner_e}")
                            continue

        except Exception as e:
            error_msg = str(e)
            print(f"[FATAL_GENERATOR_ERROR] {error_msg}")
            # Display exact error for easier debugging by the user
            cv2.putText(frame, "FATAL ERROR", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            cv2.putText(frame, error_msg[:40], (50, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)

        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        time.sleep(0.01)

@app.route('/video_feed')
def video_feed():
    if CAMERA_MODE != 'server':
        return jsonify({'success': False, 'message': 'Server stream disabled in browser camera mode'}), 503

    action = request.args.get('action')
    
    if action == 'capture':
        app_state.reset('capture')
        # Force a fresh camera open for each new capture session
        # This ensures the camera is properly initialized even after a server restart
        if not camera.is_running:
            camera.start()
        # If still not running after start(), do a full restart (handles stale handles)
        if not camera.is_running:
            threading.Thread(target=camera.restart, daemon=True).start()

        app_state.active_params = {
            'student_id': request.args.get('student_id'), 
            'class_id': request.args.get('class_id'),
            'enrollment_no': request.args.get('enrollment_no')
        }
        print(f"[FEED] CAPTURE Mode: {app_state.active_params.get('enrollment_no')}")
    elif action == 'recognize':
        app_state.reset('recognize')
        app_state.active_params = {
            'class_id': request.args.get('class_id'), 
            'subject_id': request.args.get('subject_id'),
            'section': request.args.get('section', 'A'),
            'date': request.args.get('date')
        }
        print(f"[FEED] Mode set to RECOGNIZE for Class: {app_state.active_params['class_id']}, Sec: {app_state.active_params['section']}")
    else:
        app_state.mode = 'idle'
        
    teacher_id = session.get('user_id', 1)
    
    # Session Binding: Pre-filter for student portals to avoid biometric spoofing
    expected_sid = session.get('user_id') if session.get('role') == 'student' else None
    
    print(f"[DEBUG] Video Feed started for Teacher: {teacher_id}, Session: {session.get('user_id')}, Action: {action}")
    p = app_state.active_params or {}
    print(f"[DEBUG] Params: Class={p.get('class_id')}, Sub={p.get('subject_id')}, Sec={p.get('section')}")

    return Response(generate_frames(teacher_id, expected_sid), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/camera_health')
def camera_health():
    if CAMERA_MODE != 'server':
        return jsonify({
            'status': 'healthy',
            'is_open': True,
            'has_frame': True,
            'mode': 'browser'
        })

    is_open = camera.cap is not None and camera.cap.isOpened()
    test_frame = camera.get_frame()
    has_frame = test_frame is not None
    return jsonify({
        'status': 'healthy' if (is_open and has_frame) else 'error',
        'is_open': is_open,
        'has_frame': has_frame,
        'mode': app_state.mode
    })

@app.route('/api/camera_reboot', methods=['POST'])
def camera_reboot():
    if CAMERA_MODE != 'server':
        return jsonify({'message': 'Browser camera mode active. No server sensor reboot needed.'})

    camera.restart()
    return jsonify({'message': 'Camera subsystem rebooted'})

@app.route('/api/capture_status')
def capture_status():
    return jsonify({
        'capturing': app_state.mode == 'capture', 
        'count': app_state.capture_count, 
        'progress': (app_state.capture_count / app_state.max_capture) * 100,
        'error': app_state.error,
        'matched_student': app_state.matched_student
    })

@app.route('/train', methods=['POST'])
def train_model():
    if engine.train_model_v2(db): return jsonify({'message': 'AI Optimized Successfully'})
    return jsonify({'error': 'No dataset found'}), 400

if __name__ == '__main__':
    # Start camera only once in the main process
    if CAMERA_MODE == 'server' and (os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug):
        threading.Thread(target=camera.start, daemon=True).start()
        
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=True, threaded=True)
