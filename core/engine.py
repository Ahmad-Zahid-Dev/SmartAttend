import cv2
import os
import numpy as np

class SmartAttendEngine:
    def __init__(self):
        # Load Haar Cascade with fallback
        cascade_name = 'haarcascade_frontalface_default.xml'
        self.face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + cascade_name)
        
        if self.face_cascade.empty():
            print(f"[ENGINE] Warning: System cascade not found. Trying local file...")
            self.face_cascade = cv2.CascadeClassifier(cascade_name)
            
        if self.face_cascade.empty():
            print("[ENGINE] ERROR: Could not load Haar Cascade!")
        
        self.recognizer = cv2.face.LBPHFaceRecognizer_create()
        self.dataset_dir = os.path.join(os.getcwd(), 'dataset')
        os.makedirs(self.dataset_dir, exist_ok=True)
        self.model_path = os.path.join(os.getcwd(), 'model.yml')
        
        self.model_loaded = False
        if os.path.exists(self.model_path):
            try:
                self.recognizer.read(self.model_path)
                self.model_loaded = True
            except:
                pass

    def check_duplicate(self, face_gray, threshold=50):
        """
        Checks if the provided face already exists in the trained model.
        Returns (is_duplicate, student_id, confidence)
        """
        if not self.model_loaded:
            return False, None, 100
        
        try:
            face_resized = cv2.resize(face_gray, (200, 200))
            face_equalized = cv2.equalizeHist(face_resized)
            # Apply sharpening filter to match training data
            kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
            face_final = cv2.filter2D(face_equalized, -1, kernel)
            
            label, confidence = self.recognizer.predict(face_final)
            if confidence < threshold:
                return True, label, confidence
        except Exception as e:
            print(f"[ENGINE] Duplicate check error: {e}")
            
        return False, None, 100

    def train_model_v2(self, db_instance):
        """
        Trains the LBPH model using images from the structured dataset directory.
        Path format: dataset/ClassID/<student_token>/*.jpg
        student_token may be StudentID (preferred) or legacy EnrollmentNo.
        We resolve token -> StudentID using class-aware matching.
        """
        faces = []
        labels = []
        
        if not os.path.exists(self.dataset_dir):
            return False
            
        # Recursive scan
        for class_id_folder in os.listdir(self.dataset_dir):
            class_path = os.path.join(self.dataset_dir, class_id_folder)
            if not os.path.isdir(class_path): continue
            
            for student_token in os.listdir(class_path):
                student_path = os.path.join(class_path, student_token)
                if not os.path.isdir(student_path): continue
                
                # Resolve student by token. Prefer direct student-id lookup when token is numeric.
                student = None
                if str(student_token).isdigit():
                    s_by_id = db_instance.get_student_by_id(student_token)
                    if s_by_id and str(s_by_id.get('ClassId')) == str(class_id_folder):
                        student = s_by_id

                # Legacy fallback: token may be enrollment number.
                if not student:
                    student = db_instance.get_student_by_enrollment(student_token, class_id=class_id_folder)

                if not student:
                    print(f"[ENGINE] Warning: No student mapping for token={student_token} class={class_id_folder}")
                    continue
                    
                student_id = student['id']
                
                for filename in os.listdir(student_path):
                    if filename.endswith('.jpg'):
                        try:
                            img_path = os.path.join(student_path, filename)
                            img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
                            
                            if img is not None:
                                img_resized = cv2.resize(img, (200, 200))
                                img_equalized = cv2.equalizeHist(img_resized)
                                kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
                                img_sharpened = cv2.filter2D(img_equalized, -1, kernel)
                                
                                faces.append(img_sharpened)
                                labels.append(student_id)
                        except Exception as e:
                            print(f"Error processing {img_path}: {e}")

        if faces:
            # Enhanced LBPH parameters for higher precision
            self.recognizer = cv2.face.LBPHFaceRecognizer_create(
                radius=1, 
                neighbors=8, 
                grid_x=10, # Increased from 8 for better detail
                grid_y=10, # Increased from 8 for better detail
                threshold=100.0
            )
            self.recognizer.train(faces, np.array(labels))
            self.recognizer.save(self.model_path)
            self.model_loaded = True
            print(f"[ENGINE] Trained model with {len(faces)} samples. Grid: 10x10. Precision tightened.")
            return True
        return False
        return False

    def delete_dataset(self, student_id):
        """Removes all images for a specific student and retrains."""
        files = [f for f in os.listdir(self.dataset_dir) if f.split('_')[1] == str(student_id)]
        for f in files:
            os.remove(os.path.join(self.dataset_dir, f))
        return True

engine = SmartAttendEngine()
