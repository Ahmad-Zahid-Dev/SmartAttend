import cv2
import time
import os

class VideoCamera:
    """
    Handles camera operations and frame processing for 
    Attendance and Registration modes.
    """
    def __init__(self, recognizer, capture_module, db):
        self.cap = None
        self.recognizer = recognizer
        self.capture_module = capture_module
        self.db = db
        
        # State
        self.mode = "idle"
        self.student_id = ""
        self.num_samples = 100
        self.sample_count = 0
        self.marked_ids = set()
        self.selected_dep = "All"
        self.selected_sem = ""
        self.selected_subject = ""
        self.frame_count = 0
        self.last_results = []
        self.is_running = False

    def start(self):
        if not self.cap or not self.cap.isOpened():
            # Use CAP_DSHOW for faster startup and reliability on Windows
            self.cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.is_running = True

    def stop(self):
        self.is_running = False
        if self.cap:
            self.cap.release()
            self.cap = None

    def get_frame(self):
        if not self.is_running or not self.cap:
            return None

        success, frame = self.cap.read()
        if not success:
            return None

        processed_frame = frame.copy()

        if self.mode == "attendance":
            self.frame_count += 1
            if self.frame_count == 1:
                self.db.preload_student_cache()

            # Logic: Every 4th frame for maximum speed (smooth video, fast detection)
            if self.frame_count % 4 == 0 or self.frame_count == 1:
                # Resize to 1/3 for even faster processing
                small_frame = cv2.resize(frame, (0, 0), fx=0.33, fy=0.33)
                self.last_results = self.recognizer.recognize_frame(small_frame)
                for res in self.last_results:
                    x, y, w, h = res["bbox"]
                    # Scale back up (1 / 0.33 approx 3)
                    res["bbox"] = (int(x*3.03), int(y*3.03), int(w*3.03), int(h*3.03))

            if self.recognizer.is_loaded:
                processed_frame = self.recognizer.draw_results(frame, self.last_results, db=self.db, selected_dep=self.selected_dep)

            # Mark Attendance - logic throttle
            if self.frame_count % 5 == 0:
                for r in self.last_results:
                    sid = r["student_id"]
                    if sid != "Unknown" and sid not in self.marked_ids:
                        student = self.db.get_student(sid)
                        if student:
                            # Strict department/semester check
                            if self.selected_dep != "All" and student.get("dep", "") != self.selected_dep:
                                continue
                            
                            if self.db.mark_attendance(sid, student["name"], student.get("dep", ""), self.selected_subject):
                                self.marked_ids.add(sid)
                                print(f"[NEON] Face Matched: {student['name']}")

        elif self.mode == "registration":
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self.capture_module.face_cascade.detectMultiScale(gray, 1.2, 5, (100, 100))
            
            for (x, y, w, h) in faces:
                if self.sample_count < self.num_samples:
                    self.sample_count += 1
                    face_roi = gray[y:y+h, x:x+w]
                    face_resized = cv2.resize(face_roi, (200, 200))
                    
                    data_dir = self.capture_module.get_abs_data_dir()
                    student_dir = os.path.join(data_dir, self.student_id)
                    os.makedirs(student_dir, exist_ok=True)
                    
                    img_path = os.path.join(student_dir, f"{self.student_id}_{self.sample_count}.jpg")
                    cv2.imwrite(img_path, face_resized)
                    
                    cv2.rectangle(processed_frame, (x, y), (x+w, y+h), (0, 242, 255), 2)
                else:
                    self.mode = "idle"
                    break

        return processed_frame
