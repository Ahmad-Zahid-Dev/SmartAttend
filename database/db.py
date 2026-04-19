import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from datetime import datetime
import os
import json
import time
import threading

class DatabaseManager:
    def __init__(self):
        self.project_id = 'face-recognition-5bc79'
        self._initialize_firebase()
        self.db = firestore.client()

        # ── In-Memory Cache ────────────────────────────────────────────────
        # All static/semi-static data is loaded ONCE at startup and kept here.
        # This means the app makes a handful of Firestore reads at launch and
        # then serves every API request from RAM.  Only attendance WRITES still
        # hit Firestore.
        self._cache = {
            'students':  {},   # {str(int_id): dict}
            'classes':   {},   # {doc_id: dict}
            'subjects':  {},   # {doc_id: dict}
            'teachers':  {},   # {doc_id: dict}
            'assignments': [], # list[dict]
        }
        self._attendance_cache = {}   # {(sid,sub_id,date_str): True}  — prevents duplicate writes
        self._cache_loaded = False
        self._cache_lock = threading.Lock()
        # Stats cache to prevent repeated Firestore reads on dashboard polls
        self._stats_cache = {'data': None, 'ts': 0}  # Cached for 60 seconds
        self._STATS_TTL = 60  # seconds

        try:
            self._load_cache()
        except Exception as e:
            print(f"[DB] Cache load skipped (quota?): {e}")

        # Only seed if Firestore is reachable AND cache is empty (prevents quota burn on restarts)
        try:
            if not self._cache['teachers'] and not self._cache['classes']:
                self._seed_data()
        except Exception as e:
            print(f"[DB] Seed skipped (quota?): {e}")

    # ──────────────────────────────────────────────────────────────────────
    # Firebase Init
    # ──────────────────────────────────────────────────────────────────────
    def _initialize_firebase(self):
        if not firebase_admin._apps:
            service_json = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON', '').strip()
            if service_json:
                try:
                    info = json.loads(service_json)
                    cred = credentials.Certificate(info)
                    firebase_admin.initialize_app(cred)
                    print("[FIREBASE] Initialized from FIREBASE_SERVICE_ACCOUNT_JSON.")
                    return
                except Exception as e:
                    print(f"[FIREBASE] Invalid FIREBASE_SERVICE_ACCOUNT_JSON: {e}")

            key_path = os.path.join(os.getcwd(), 'serviceAccountKey.json')
            if os.path.exists(key_path):
                cred = credentials.Certificate(key_path)
                firebase_admin.initialize_app(cred)
                print("[FIREBASE] Initialized with Service Account Key.")
            else:
                firebase_admin.initialize_app(options={'projectId': self.project_id})
                print(f"[FIREBASE] Initialized with project ID only. Set GOOGLE_APPLICATION_CREDENTIALS if needed.")

    # ──────────────────────────────────────────────────────────────────────
    # Cache Loading  (called once at startup; ~20-30 reads total)
    # ──────────────────────────────────────────────────────────────────────
    def _load_cache(self):
        """Bulk-load all static collections into RAM. Total cost: 4 collection reads."""
        print("[CACHE] Loading all data from Firestore...")
        try:
            with self._cache_lock:
                # Students
                for d in self.db.collection('students').get():
                    data = d.to_dict()
                    data['_doc_id'] = d.id
                    key = str(data.get('int_id', d.id))
                    self._cache['students'][key] = data

                # Classes
                for d in self.db.collection('classes').get():
                    data = d.to_dict()
                    data['_doc_id'] = d.id
                    self._cache['classes'][d.id] = data

                # Subjects
                for d in self.db.collection('subjects').get():
                    data = d.to_dict()
                    data['_doc_id'] = d.id
                    self._cache['subjects'][d.id] = data

                # Teachers
                for d in self.db.collection('teachers').get():
                    data = d.to_dict()
                    data['_doc_id'] = d.id
                    self._cache['teachers'][d.id] = data

                # Assignments
                asgn_list = []
                for d in self.db.collection('assignments').get():
                    ad = d.to_dict()
                    ad['id'] = d.id
                    asgn_list.append(ad)
                self._cache['assignments'] = asgn_list

                # Pre-load today's attendance to know what's already marked
                today_str = datetime.now().strftime('%Y-%m-%d')
                for d in self.db.collection('attendance_logs').where(
                        filter=FieldFilter('DateStr', '==', today_str)).get():
                    data = d.to_dict()
                    key = (str(data.get('StudentId')), str(data.get('SubjectId')), str(data.get('DateStr')))
                    self._attendance_cache[key] = True

                self._cache_loaded = True
            print(f"[CACHE] Loaded: {len(self._cache['students'])} students, "
                  f"{len(self._cache['classes'])} classes, "
                  f"{len(self._cache['subjects'])} subjects, "
                  f"{len(self._cache['teachers'])} teachers.")
        except Exception as e:
            print(f"[CACHE ERROR] Failed to load cache: {e}")

    def invalidate_cache(self, collection=None):
        """Call after any write to refresh the relevant cache section."""
        if collection:
            # Simple targeted refresh
            with self._cache_lock:
                if collection == 'students':
                    self._cache['students'] = {}
                    for d in self.db.collection('students').get():
                        data = d.to_dict(); data['_doc_id'] = d.id
                        key = str(data.get('int_id', d.id))
                        self._cache['students'][key] = data
                elif collection == 'classes':
                    self._cache['classes'] = {}
                    for d in self.db.collection('classes').get():
                        data = d.to_dict(); data['_doc_id'] = d.id
                        self._cache['classes'][d.id] = data
                elif collection == 'subjects':
                    self._cache['subjects'] = {}
                    for d in self.db.collection('subjects').get():
                        data = d.to_dict(); data['_doc_id'] = d.id
                        self._cache['subjects'][d.id] = data
                elif collection == 'teachers':
                    self._cache['teachers'] = {}
                    for d in self.db.collection('teachers').get():
                        data = d.to_dict(); data['_doc_id'] = d.id
                        self._cache['teachers'][d.id] = data
                elif collection == 'assignments':
                    asgn_list = []
                    for d in self.db.collection('assignments').get():
                        ad = d.to_dict(); ad['id'] = d.id
                        asgn_list.append(ad)
                    self._cache['assignments'] = asgn_list
        else:
            self._load_cache()

    # ──────────────────────────────────────────────────────────────────────
    # Seed Data
    # ──────────────────────────────────────────────────────────────────────
    def _seed_data(self):
        # Seed Admin Teacher
        teachers_ref = self.db.collection('teachers')
        admin_query = teachers_ref.where(filter=FieldFilter('Username', '==', 'admin')).limit(1).get()
        if not admin_query:
            teachers_ref.add({
                'Name': 'System Admin', 'Username': 'admin', 'Password': 'admin123',
                'Role': 'admin', 'Specialization': 'Management',
                'IsActive': True, 'CreatedDate': firestore.SERVER_TIMESTAMP
            })
            print("[SEED] Created admin account.")

        # Seed Default Classes
        classes_ref = self.db.collection('classes')
        for name, short in [
            ('Bachelor of Computer Applications', 'BCA'),
            ('Master of Computer Applications', 'MCA'),
            ('Bachelor of Technology', 'BTech'),
            ('Master of Technology', 'MTech')
        ]:
            if not classes_ref.where(filter=FieldFilter('ShortName', '==', short)).limit(1).get():
                classes_ref.add({'ClassName': name, 'ShortName': short, 'IsActive': True,
                                 'CreatedDate': firestore.SERVER_TIMESTAMP})
                print(f"[SEED] Added Class: {short}")

        # Seed Default Subjects
        subjects_ref = self.db.collection('subjects')
        it_subjects = [
            ('Python Programming', 'PY101'), ('C Programming', 'C101'), ('C++ OOPs', 'CPP201'),
            ('DAA (Algorithms)', 'CS301'), ('Computer Networks', 'NW401'), ('Cyber Security', 'SEC601'),
            ('Engineering Mathematics', 'MA101'), ('Discrete Mathematics', 'MA201'),
            ('Database Management', 'DB201'), ('Web Development', 'WEB301'),
            ('Data Structures', 'DSA202'), ('Operating Systems', 'OS402'),
            ('AI & Machine Learning', 'AI505'), ('Cloud Computing', 'CLD602'),
            ('Software Engineering', 'SE302'), ('Mobile App Dev', 'MOB303'),
            ('Data Science', 'DS506'), ('Internet of Things', 'IOT603'),
            ('Computer Graphics', 'CG403'), ('Theory of Comp.', 'TC404'),
            ('Compiler Design', 'CD507'), ('Blockchain Tech', 'BC604'),
            ('Full Stack Lab', 'FSL304'), ('Microprocessors', 'MP203'),
            ('Comp. Architecture', 'COA204'), ('Distributed Sys.', 'DS508'),
            ('Parallel Computing', 'PC509'), ('NLP & Text Mining', 'NLP605'),
            ('Big Data Analytics', 'BD606'), ('Software Testing', 'ST305'),
            ('UI/UX Design', 'UX306'), ('IT Proj. Management', 'PM307'),
            ('Digital Imaging', 'DI405')
        ]
        for name, code in it_subjects:
            # Check against in-memory cache — avoids 33 Firestore reads on every startup
            already_exists = any(s.get('SubjectCode') == code for s in self._cache['subjects'].values())
            if not already_exists:
                subjects_ref.add({'SubjectName': name, 'SubjectCode': code, 'ClassId': None,
                                  'IsActive': True, 'CreatedDate': firestore.SERVER_TIMESTAMP})
                print(f"[SEED] Added Subject: {code}")

    # ──────────────────────────────────────────────────────────────────────
    # Counter (for LBPH integer IDs)
    # ──────────────────────────────────────────────────────────────────────
    def _get_next_id(self, counter_name):
        counter_ref = self.db.collection('counters').document(counter_name)
        @firestore.transactional
        def update_counter(transaction, ref):
            snapshot = ref.get(transaction=transaction)
            if snapshot.exists:
                new_val = snapshot.get('value') + 1
                transaction.update(ref, {'value': new_val})
                return new_val
            else:
                transaction.set(ref, {'value': 1})
                return 1
        transaction = self.db.transaction()
        return update_counter(transaction, counter_ref)

    # ──────────────────────────────────────────────────────────────────────
    # Auth (still hits Firestore — login is rare, acceptable)
    # ──────────────────────────────────────────────────────────────────────
    def get_user(self, username, password, role=None):
        print(f"[AUTH] Attempting login: {username} as {role}")

        # ── Check cache first ──
        for tid, t in self._cache['teachers'].items():
            if str(t.get('Username', '')).strip() == str(username).strip() and \
               str(t.get('Password', '')).strip() == str(password).strip():
                if t.get('IsActive') is False:
                    print(f"[AUTH] Teacher {username} is marked inactive.")
                    return None
                user = dict(t)
                user['id'] = tid
                user['Role'] = 'admin' if str(username).lower() == 'admin' else 'faculty'
                print(f"[AUTH] SUCCESS (cache): {user['Role']}")
                return user

        if role == 'student':
            for sid, s in self._cache['students'].items():
                if str(s.get('Username', '')).strip() == str(username).strip() and \
                   str(s.get('Password', '')).strip() == str(password).strip():
                    if s.get('IsActive') is False:
                        return None
                    user = dict(s)
                    user['id'] = s.get('int_id') or s.get('_doc_id')
                    user['Role'] = 'student'
                    # Attach class name
                    cid = str(s.get('ClassId', ''))
                    cls = self._cache['classes'].get(cid, {})
                    user['ClassName'] = cls.get('ShortName') or cls.get('ClassName', '')
                    print(f"[AUTH] SUCCESS (cache): student")
                    return user
            return None

        # ── Fallback: hit Firestore if cache miss (e.g. first startup) ──
        print(f"[AUTH] Cache miss — querying Firestore for {username}")
        if role == 'student':
            docs = self.db.collection('students')\
                .where(filter=FieldFilter('Username', '==', str(username)))\
                .where(filter=FieldFilter('Password', '==', str(password)))\
                .limit(1).get()
            if docs:
                user = docs[0].to_dict()
                if user.get('IsActive') is False: return None
                user['id'] = user.get('int_id') or docs[0].id
                user['Role'] = 'student'
                cid = str(user.get('ClassId', ''))
                class_doc = self.db.collection('classes').document(cid).get()
                if class_doc.exists:
                    user['ClassName'] = class_doc.to_dict().get('ShortName', '')
                return user
            return None

        docs = self.db.collection('teachers')\
            .where(filter=FieldFilter('Username', '==', str(username)))\
            .where(filter=FieldFilter('Password', '==', str(password)))\
            .limit(1).get()
        if docs:
            user = docs[0].to_dict()
            if user.get('IsActive') is False: return None
            user['id'] = docs[0].id
            user['Role'] = 'admin' if str(username).lower() == 'admin' else 'faculty'
            print(f"[AUTH] SUCCESS (Firestore): {user['Role']}")
            return user
        print(f"[AUTH] FAILED: No match for {username}")
        return None

    # ──────────────────────────────────────────────────────────────────────
    # Profile
    # ──────────────────────────────────────────────────────────────────────
    def get_teacher_profile(self, tid):
        t = self._cache['teachers'].get(str(tid))
        if t:
            result = dict(t)
            result['id'] = tid
            return result
        # Firestore fallback
        doc = self.db.collection('teachers').document(str(tid)).get()
        if doc.exists:
            d = doc.to_dict(); d['id'] = doc.id
            return d
        return None

    def get_student_profile(self, sid):
        s = self._cache['students'].get(str(sid))
        if s:
            result = dict(s)
            result['id'] = s.get('int_id') or s.get('_doc_id')
            cid = str(s.get('ClassId', ''))
            cls = self._cache['classes'].get(cid, {})
            result['ClassName'] = cls.get('ShortName') or cls.get('ClassName', '')
            return result
        # Firestore fallback
        doc = self.db.collection('students').document(str(sid)).get()
        if doc.exists:
            d = doc.to_dict(); d['id'] = d.get('int_id') or sid
            return d
        return None

    # ──────────────────────────────────────────────────────────────────────
    # Class Management  (cache-first)
    # ──────────────────────────────────────────────────────────────────────
    def get_classes(self, active_only=True):
        results = []
        for cid, c in self._cache['classes'].items():
            if active_only and c.get('IsActive') is False:
                continue
            results.append({**c, 'id': cid})
        return results

    def add_class(self, name, short_name):
        self.db.collection('classes').add({
            'ClassName': name, 'ShortName': short_name,
            'IsActive': True, 'CreatedDate': firestore.SERVER_TIMESTAMP
        })
        self.invalidate_cache('classes')
        return True

    # ──────────────────────────────────────────────────────────────────────
    # Subject Management  (cache-first)
    # ──────────────────────────────────────────────────────────────────────
    def get_subjects(self):
        return [{**s, 'id': sid} for sid, s in self._cache['subjects'].items()]

    def get_subjects_by_class(self, class_id):
        if not class_id:
            return self.get_subjects()
        result = [{**s, 'id': sid} for sid, s in self._cache['subjects'].items()
                  if str(s.get('ClassId')) == str(class_id)]
        if not result:
            result = self.get_subjects()
        return result

    def add_subject(self, name, code, class_id=None):
        self.db.collection('subjects').add({
            'SubjectName': name, 'SubjectCode': code,
            'ClassId': class_id, 'IsActive': True,
            'CreatedDate': firestore.SERVER_TIMESTAMP
        })
        self.invalidate_cache('subjects')
        return True

    # ──────────────────────────────────────────────────────────────────────
    # Teacher Management  (cache-first)
    # ──────────────────────────────────────────────────────────────────────
    def get_teachers(self, active_only=True):
        results = []
        for tid, t in self._cache['teachers'].items():
            if str(t.get('Username', '')).lower() == 'admin':
                continue
            if active_only and t.get('IsActive') is False:
                continue
            results.append({**t, 'id': tid})
        return results

    def add_teacher(self, name, username, password, specialization,
                    subject_ids=None, faculty_id=None, department=None,
                    email=None, phone=None):
        doc_ref = self.db.collection('teachers').add({
            'Name': name, 'Username': username, 'Password': password,
            'Specialization': specialization, 'FacultyID': faculty_id,
            'Department': department, 'Email': email, 'Phone': phone,
            'IsActive': True, 'CreatedDate': firestore.SERVER_TIMESTAMP
        })
        teacher_id = doc_ref[1].id

        if subject_ids and isinstance(subject_ids, list):
            depts = [d.strip() for d in (department or "GEN").split(',')]
            class_ids = []
            for dep in depts:
                matched = [cid for cid, c in self._cache['classes'].items()
                           if c.get('ShortName') == dep]
                class_ids.append(matched[0] if matched else 'GEN')

            for sid in subject_ids:
                for cid in class_ids:
                    self.assign_teacher(teacher_id, cid, sid, name)

        self.invalidate_cache('teachers')
        self.invalidate_cache('assignments')
        return teacher_id

    # ──────────────────────────────────────────────────────────────────────
    # Assignment Management  (cache-first)
    # ──────────────────────────────────────────────────────────────────────
    def assign_teacher(self, teacher_id, class_id, subject_id, teacher_name=None):
        # Check duplicate using in-memory cache — avoids a live Firestore read per assignment
        existing = any(
            str(a.get('teacher_id')) == str(teacher_id) and
            str(a.get('class_id')) == str(class_id) and
            str(a.get('subject_id')) == str(subject_id)
            for a in self._cache['assignments']
        )
        if existing:
            return False

        cls = self._cache['classes'].get(str(class_id), {})
        sub = self._cache['subjects'].get(str(subject_id), {})
        teacher = self._cache['teachers'].get(str(teacher_id), {})
        name = teacher_name or teacher.get('Name', 'Unknown')

        self.db.collection('assignments').add({
            'teacher_id': teacher_id,
            'class_id': class_id,
            'subject_id': subject_id,
            'teacher_name': name,
            'class_name': cls.get('ShortName') or cls.get('ClassName', 'Unknown'),
            'subject_name': sub.get('SubjectName', 'Unknown'),
            'subject_code': sub.get('SubjectCode', ''),
            'IsActive': True,
            'CreatedDate': firestore.SERVER_TIMESTAMP
        })
        self.invalidate_cache('assignments')
        return True

    def get_teacher_assignments(self, teacher_id):
        results = []
        for a in self._cache['assignments']:
            if str(a.get('teacher_id')) == str(teacher_id):
                # Enhance with latest cache data if missing (handles old records)
                asgn = dict(a)
                sid = str(asgn.get('subject_id'))
                cid = str(asgn.get('class_id'))
                
                # Dynamic join from cache
                sub = self._cache['subjects'].get(sid, {})
                cls = self._cache['classes'].get(cid, {})
                
                if not asgn.get('subject_code'):
                    asgn['subject_code'] = sub.get('SubjectCode', '')
                if not asgn.get('subject_name') or asgn.get('subject_name') == 'Unknown':
                    asgn['subject_name'] = sub.get('SubjectName', 'Unknown')
                if not asgn.get('class_name') or asgn.get('class_name') == 'Unknown':
                    asgn['class_name'] = cls.get('ShortName') or cls.get('ClassName', 'Unknown')
                
                # Mirror to PascalCase to support all JS templates
                asgn['SubjectCode'] = asgn['subject_code']
                asgn['SubjectName'] = asgn['subject_name']
                asgn['ClassName'] = asgn['class_name']
                
                results.append(asgn)
        print(f"[AUTH] Enhanced {len(results)} assignments for teacher {teacher_id}")
        return results

    def get_all_assignments(self):
        return list(self._cache['assignments'])

    def get_faculty_db(self, active_only=False):
        """
        Returns faculty directory rows with course + subject allocations.
        Used by admin Faculty DB screen.
        """
        teachers = self.get_teachers(active_only=active_only)

        # Build teacher -> assignments map from cache with fresh class/subject joins.
        by_teacher = {}
        for a in self._cache['assignments']:
            tid = str(a.get('teacher_id', ''))
            cid = str(a.get('class_id', ''))
            sid = str(a.get('subject_id', ''))

            cls = self._cache['classes'].get(cid, {})
            sub = self._cache['subjects'].get(sid, {})

            item = {
                'class_id': cid,
                'class_name': a.get('class_name') or cls.get('ShortName') or cls.get('ClassName', 'Unknown'),
                'subject_id': sid,
                'subject_name': a.get('subject_name') or sub.get('SubjectName', 'Unknown'),
                'subject_code': a.get('subject_code') or sub.get('SubjectCode', '')
            }

            if tid not in by_teacher:
                by_teacher[tid] = []

            # Dedupe repeated assignment entries.
            exists = any(
                str(x.get('class_id')) == str(item['class_id']) and
                str(x.get('subject_id')) == str(item['subject_id'])
                for x in by_teacher[tid]
            )
            if not exists:
                by_teacher[tid].append(item)

        rows = []
        for t in teachers:
            tid = str(t.get('id'))
            assignments = by_teacher.get(tid, [])
            assignments.sort(key=lambda x: (str(x.get('class_name', '')), str(x.get('subject_code', '')), str(x.get('subject_name', ''))))

            courses = sorted({x.get('class_name', 'Unknown') for x in assignments})
            subjects = []
            for x in assignments:
                code = str(x.get('subject_code', '')).strip()
                name = str(x.get('subject_name', 'Unknown')).strip()
                subjects.append(f"[{code}] {name}" if code else name)

            rows.append({
                'id': tid,
                'Name': t.get('Name', ''),
                'Username': t.get('Username', ''),
                'Email': t.get('Email'),
                'Phone': t.get('Phone'),
                'Department': t.get('Department'),
                'IsActive': t.get('IsActive', True),
                'courses': courses,
                'subjects': subjects,
                'assignments': assignments
            })

        return rows

    def delete_assignment(self, asgn_id):
        self.db.collection('assignments').document(str(asgn_id)).delete()
        self.invalidate_cache('assignments')
        return True

    # ──────────────────────────────────────────────────────────────────────
    # Student Management  (cache-first)
    # ──────────────────────────────────────────────────────────────────────
    def add_student(self, full_name, class_id, enrollment_no, username, password,
                    phone="", email="", dob=None, address="", section='A', batch='2024'):
        int_id = self._get_next_id('students')
        data = {
            'int_id': int_id, 'EnrollmentNo': enrollment_no,
            'Username': username, 'Password': password,
            'FullName': full_name, 'ClassId': class_id,
            'Section': section, 'Batch': batch,
            'PhoneNo': phone, 'EmailId': email, 'DOB': dob,
            'Address': address, 'IsActive': True,
            'CreatedDate': firestore.SERVER_TIMESTAMP
        }
        self.db.collection('students').document(str(int_id)).set(data)
        # Update cache immediately — no extra read needed
        data['_doc_id'] = str(int_id)
        self._cache['students'][str(int_id)] = data
        return int_id

    def get_student_by_id(self, sid):
        s = self._cache['students'].get(str(sid))
        if s:
            result = dict(s)
            result['id'] = s.get('int_id') or s.get('_doc_id')
            cid = str(s.get('ClassId', ''))
            cls = self._cache['classes'].get(cid, {})
            result['ClassName'] = cls.get('ShortName') or cls.get('ClassName', '')
            return result
        # Firestore fallback
        doc = self.db.collection('students').document(str(sid)).get()
        if doc.exists:
            data = doc.to_dict()
            data['id'] = data.get('int_id') or sid
            data['_doc_id'] = doc.id
            self._cache['students'][str(sid)] = data  # populate cache
            return data
        return None

    def get_student_by_enrollment(self, eno, class_id=None):
        candidates = []
        for s in self._cache['students'].values():
            if str(s.get('EnrollmentNo', '')) == str(eno):
                candidates.append(s)

        if not candidates:
            return None

        if class_id is not None:
            class_id_str = str(class_id)
            scoped = [s for s in candidates if str(s.get('ClassId')) == class_id_str]
            if scoped:
                result = dict(scoped[0])
                result['id'] = scoped[0].get('int_id')
                return result

        # If enrollment is duplicated across classes and no class is provided,
        # avoid returning a potentially wrong student record.
        if len(candidates) > 1 and class_id is None:
            return None

        result = dict(candidates[0])
        result['id'] = candidates[0].get('int_id')
        return result

    def get_students_by_class(self, class_id):
        return [
            {'id': s.get('_doc_id'), 'EnrollmentNo': s.get('EnrollmentNo'),
             'name': s.get('FullName'), 'ClassId': s.get('ClassId'),
             'Section': s.get('Section'), 'Batch': s.get('Batch'),
             'int_id': s.get('int_id')}
            for s in self._cache['students'].values()
            if str(s.get('ClassId')) == str(class_id) and s.get('IsActive') is not False
        ]

    def get_students_by_filter(self, class_id, section=None):
        return [
            {'id': s.get('_doc_id'), 'EnrollmentNo': s.get('EnrollmentNo'),
             'name': s.get('FullName'), 'int_id': s.get('int_id')}
            for s in self._cache['students'].values()
            if str(s.get('ClassId')) == str(class_id)
            and s.get('IsActive') is not False
            and (not section or str(s.get('Section')) == str(section))
        ]

    def get_sections_by_class(self, class_id):
        sections = {str(s.get('Section')) for s in self._cache['students'].values()
                    if str(s.get('ClassId')) == str(class_id) and s.get('Section')}
        if not sections:
            return [{'Section': 'A'}, {'Section': 'B'}]
        return [{'Section': sec} for sec in sorted(sections)]

    def get_all_students(self):
        results = []
        for s in self._cache['students'].values():
            cid = str(s.get('ClassId', ''))
            cls = self._cache['classes'].get(cid, {})
            results.append({
                'id': s.get('int_id'),
                'EnrollmentNo': s.get('EnrollmentNo'),
                'name': s.get('FullName'),
                'email': s.get('EmailId'),
                'Section': s.get('Section'),
                'Batch': s.get('Batch'),
                'ClassId': s.get('ClassId'),
                'ClassName': cls.get('ShortName') or cls.get('ClassName', 'Unknown'),
                'IsActive': s.get('IsActive')
            })
        return results

    def soft_delete_student(self, sid, status):
        self.db.collection('students').document(str(sid)).update({'IsActive': status})
        if str(sid) in self._cache['students']:
            self._cache['students'][str(sid)]['IsActive'] = status
        return True

    # ──────────────────────────────────────────────────────────────────────
    # Attendance Logging  (still writes to Firestore, but duplicate-checks
    # via in-memory cache — avoids double read before every write)
    # ──────────────────────────────────────────────────────────────────────
    def log_attendance(self, sid, cid, tid, sub_id, section='A', date_str=None):
        try:
            sid = int(sid)
        except Exception:
            pass

        if not date_str:
            date_str = datetime.now().strftime('%Y-%m-%d')

        cache_key = (str(sid), str(sub_id), date_str)
        if cache_key in self._attendance_cache:
            print(f"[DB] SKIPPED (memory cache): {sid} already marked on {date_str}")
            return False

        # Memory cache is authoritative after startup _load_cache pre-loads today's logs.
        # Skip the extra live Firestore read — it costs 1 read per recognition hit.
        # The cache_key check above is sufficient; if the app was restarted mid-day,
        # _load_cache() already pre-populated today's keys at boot time.
        self.db.collection('attendance_logs').add({
            'StudentId': sid, 'ClassId': cid, 'TeacherId': tid,
            'SubjectId': sub_id, 'Section': section,
            'DateStr': date_str, 'DateTime': firestore.SERVER_TIMESTAMP,
            'Status': 'Present'
        })
        self._attendance_cache[cache_key] = True
        print(f"[DB] --> SUCCESS: Attendance marked for student {sid}")
        return True

    # ──────────────────────────────────────────────────────────────────────
    # Attendance Queries  (cache-first for student/subject lookups)
    # ──────────────────────────────────────────────────────────────────────
    def get_attendance_today(self, class_id, subject_id=None, section=None, date_str=None):
        if not date_str:
            date_str = datetime.now().strftime('%Y-%m-%d')

        query = self.db.collection('attendance_logs')\
            .where(filter=FieldFilter('ClassId', '==', class_id))\
            .where(filter=FieldFilter('DateStr', '==', date_str))
        if subject_id:
            query = query.where(filter=FieldFilter('SubjectId', '==', subject_id))
        if section:
            query = query.where(filter=FieldFilter('Section', '==', section))

        results = []
        for d in query.get():
            data = d.to_dict()
            sid_key = str(data.get('StudentId'))
            sub_key = str(data.get('SubjectId', ''))

            # Use cache for lookups — zero extra Firestore reads
            s = self._cache['students'].get(sid_key, {})
            sub = self._cache['subjects'].get(sub_key, {})

            data['FullName'] = s.get('FullName', 'Unknown')
            data['EnrollmentNo'] = s.get('EnrollmentNo', 'N/A')
            data['SubjectCode'] = sub.get('SubjectCode', 'N/A')
            data['StudentId'] = sid_key

            dt = data.get('DateTime')
            if hasattr(dt, 'isoformat'):
                data['DateTime'] = dt.isoformat()

            results.append(data)

        results.sort(key=lambda x: x.get('DateTime', ''), reverse=True)
        return results

    def get_student_attendance(self, sid):
        try:
            sid = int(sid)
        except Exception:
            pass

        logs = self.db.collection('attendance_logs')\
            .where(filter=FieldFilter('StudentId', '==', sid)).get()
        if not logs and isinstance(sid, int):
            logs = self.db.collection('attendance_logs')\
                .where(filter=FieldFilter('StudentId', '==', str(sid))).get()

        results = []
        for log in logs:
            data = log.to_dict()
            cid = str(data.get('ClassId', ''))
            tid = str(data.get('TeacherId', ''))
            sub_id = str(data.get('SubjectId', ''))

            cls = self._cache['classes'].get(cid, {})
            teacher = self._cache['teachers'].get(tid, {})
            sub = self._cache['subjects'].get(sub_id, {})

            data['ClassName'] = cls.get('ClassName', 'Unknown')
            data['TeacherName'] = teacher.get('Name', 'System')
            data['SubjectName'] = sub.get('SubjectName', 'General')

            dt = data.get('DateTime')
            if hasattr(dt, 'isoformat'):
                data['DateTime'] = dt.isoformat()

            results.append(data)

        results.sort(key=lambda x: x.get('DateTime', ''), reverse=True)
        return results

    def get_student_attendance_by_date(self, sid, date_str):
        try:
            sid = int(sid)
        except Exception:
            pass

        logs = self.db.collection('attendance_logs')\
            .where(filter=FieldFilter('StudentId', '==', sid))\
            .where(filter=FieldFilter('DateStr', '==', date_str)).get()
        if not logs and isinstance(sid, int):
            logs = self.db.collection('attendance_logs')\
                .where(filter=FieldFilter('StudentId', '==', str(sid)))\
                .where(filter=FieldFilter('DateStr', '==', date_str)).get()

        results = []
        for log in logs:
            data = log.to_dict()
            tid = str(data.get('TeacherId', ''))
            sub_id = str(data.get('SubjectId', ''))
            teacher = self._cache['teachers'].get(tid, {})
            sub = self._cache['subjects'].get(sub_id, {})
            data['TeacherName'] = teacher.get('Name', 'System')
            data['SubjectName'] = sub.get('SubjectName', 'General')
            dt = data.get('DateTime')
            if hasattr(dt, 'strftime'):
                data['Time'] = dt.strftime('%H:%M:%S')
            results.append(data)
        return results

    def get_student_attendance_by_day(self, sid, day_name):
        all_logs = self.get_student_attendance(sid)
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        filtered = [l for l in all_logs
                    if l.get('DateTime') and
                    days[datetime.fromisoformat(l['DateTime']).weekday()] == day_name]
        subjects_stats = {}
        for l in filtered:
            sub = l.get('SubjectName', 'General')
            if sub not in subjects_stats:
                subjects_stats[sub] = {'frequency': 0, 'last_present': l.get('DateStr')}
            subjects_stats[sub]['frequency'] += 1
        return [{'SubjectName': sub, 'Frequency': s['frequency'], 'LastPresent': s['last_present']}
                for sub, s in subjects_stats.items()]

    def get_student_summary(self, sid):
        """Count attendance using in-memory attendance cache — zero Firestore reads."""
        try:
            int_id = int(sid)
        except Exception:
            int_id = sid
        sid_str = str(int_id)
        # Count all distinct attendance entries in the in-memory cache for this student
        present_count = sum(1 for (s, _, _) in self._attendance_cache if s == sid_str)
        if present_count > 0:
            return {'present': present_count}
        # Fallback: live read if cache is empty (fresh startup with no attendance yet)
        logs = self.db.collection('attendance_logs')\
            .where(filter=FieldFilter('StudentId', '==', int_id)).get()
        if not logs:
            logs = self.db.collection('attendance_logs')\
                .where(filter=FieldFilter('StudentId', '==', sid_str)).get()
        # Populate cache from these results to avoid future reads
        today_str = datetime.now().strftime('%Y-%m-%d')
        for l in logs:
            d = l.to_dict()
            key = (str(d.get('StudentId')), str(d.get('SubjectId', '')), str(d.get('DateStr', '')))
            self._attendance_cache[key] = True
        return {'present': len(logs)}

    def get_cumulative_attendance(self, student_id):
        # Use cache instead of hitting Firestore for the student document
        student_data = self._cache['students'].get(str(student_id))
        if not student_data:
            # Fallback to Firestore only if cache miss
            student_doc = self.db.collection('students').document(str(student_id)).get()
            if not student_doc.exists:
                return []
            student_data = student_doc.to_dict()

        class_id = str(student_data.get('ClassId'))
        section = str(student_data.get('Section', 'A')).strip()
        target_int_id = str(student_data.get('int_id', student_id))

        class_logs = self.db.collection('attendance_logs')\
            .where(filter=FieldFilter('ClassId', '==', class_id)).get()

        def _norm_section(val):
            return str(val or '').strip().upper()

        student_section_norm = _norm_section(section)

        sessions_data = {}
        student_data_map = {}
        seen_subject_ids = set()
        for l in class_logs:
            data = l.to_dict()
            log_section_norm = _norm_section(data.get('Section'))

            # A blank section means faculty marked attendance for the full class.
            applies_to_student = (
                not log_section_norm or
                not student_section_norm or
                log_section_norm == student_section_norm
            )
            if not applies_to_student:
                continue

            sub_id = str(data.get('SubjectId'))
            if not sub_id:
                continue
            seen_subject_ids.add(sub_id)
            date_str = str(data.get('DateStr'))
            curr_sid = str(data.get('StudentId'))
            if sub_id not in sessions_data:
                sessions_data[sub_id] = set()
            sessions_data[sub_id].add(date_str)
            if curr_sid == target_int_id:
                if sub_id not in student_data_map:
                    student_data_map[sub_id] = set()
                student_data_map[sub_id].add(date_str)

        # Base subject list from class mapping, then append any subject found in logs.
        class_subject_ids = [sid for sid, s in self._cache['subjects'].items()
                             if str(s.get('ClassId')) == class_id]
        ordered_subject_ids = list(class_subject_ids)
        for sid in sorted(seen_subject_ids):
            if sid not in ordered_subject_ids:
                ordered_subject_ids.append(sid)

        today_str = datetime.now().strftime('%Y-%m-%d')
        report = []
        for sid in ordered_subject_ids:
            s = self._cache['subjects'].get(sid, {})
            held_dates = sessions_data.get(sid, set())
            present_dates = student_data_map.get(sid, set())
            held = len(held_dates)
            present = len(present_dates)
            percentage = round((present / held * 100), 2) if held > 0 else 0
            sorted_dates = sorted(list(present_dates), reverse=True)
            report.append({
                'subject_code': s.get('SubjectCode', 'N/A'),
                'subject_name': s.get('SubjectName', 'Unknown'),
                'held': held, 'present': present, 'percentage': percentage,
                'today_status': 'PRESENT' if today_str in present_dates else 'NOT MARKED',
                'last_attended': sorted_dates[0] if sorted_dates else 'Never'
            })
        return report

    def get_all_reports(self):
        logs = self.db.collection('attendance_logs')\
            .order_by('DateTime', direction=firestore.Query.DESCENDING).limit(100).get()
        results = []
        for l in logs:
            data = l.to_dict()
            sid_key = str(data.get('StudentId'))
            cid_key = str(data.get('ClassId', ''))
            tid_key = str(data.get('TeacherId', ''))
            sub_key = str(data.get('SubjectId', ''))
            s = self._cache['students'].get(sid_key, {})
            cls = self._cache['classes'].get(cid_key, {})
            teacher = self._cache['teachers'].get(tid_key, {})
            sub = self._cache['subjects'].get(sub_key, {})
            dt = data.get('DateTime')
            results.append({
                'id': l.id[:8],
                'student_name': s.get('FullName', 'Unknown'),
                'class_name': cls.get('ShortName', 'N/A'),
                'teacher_name': teacher.get('Name', 'System'),
                'subject_name': sub.get('SubjectName', 'N/A'),
                'date_time': dt.strftime('%Y-%m-%d %H:%M') if dt and hasattr(dt, 'strftime') else 'N/A'
            })
        return results

    def get_stats(self):
        """Returns dashboard stats. Cached for 60s to prevent dashboard polling from draining quota."""
        now = time.time()
        if self._stats_cache['data'] and (now - self._stats_cache['ts']) < self._STATS_TTL:
            return self._stats_cache['data']

        today_str = datetime.now().strftime('%Y-%m-%d')
        # Students count from in-memory cache — zero reads
        total_students = sum(1 for s in self._cache['students'].values()
                             if s.get('IsActive') is not False)
        # Count today's present students from in-memory attendance cache — zero reads
        present_sids = {sid for (sid, _, date) in self._attendance_cache if date == today_str}
        # Fallback: live read only if attendance cache is completely empty (first run of the day)
        if not self._attendance_cache:
            today_logs = self.db.collection('attendance_logs')\
                .where(filter=FieldFilter('DateStr', '==', today_str)).get()
            for l in today_logs:
                d = l.to_dict()
                key = (str(d.get('StudentId')), str(d.get('SubjectId', '')), today_str)
                self._attendance_cache[key] = True
            present_sids = {sid for (sid, _, date) in self._attendance_cache if date == today_str}

        result = {
            'total_students': total_students,
            'today_attendance': len(present_sids)
        }
        self._stats_cache = {'data': result, 'ts': now}
        return result

    def delete_item(self, collection, doc_id):
        self.db.collection(collection).document(str(doc_id)).delete()
        self.invalidate_cache(collection)
        return True


db = DatabaseManager()
