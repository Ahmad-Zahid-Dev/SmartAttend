"""
Face Recognizer Module
======================
Performs real-time face recognition using the trained LBPH model.

Recognition Pipeline:
1. Capture frame from webcam
2. Convert to grayscale
3. Detect faces using Haar Cascade  
4. For each detected face:
   a. Crop and resize to 200x200 (same as training size)
   b. Pass through LBPH recognizer → get (predicted_label, confidence)
   c. Map predicted_label to student_id using the saved ID map
   d. If confidence < threshold, it's a known face → fetch name from DB
   e. If confidence >= threshold, it's an unknown face

Confidence Score Interpretation:
- The confidence is actually a "distance" metric (lower = better match)
- Typical thresholds for LBPH:
  - < 50: Very confident match
  - 50-80: Reasonably confident match 
  - > 80: Low confidence, possibly unknown face
- We use 70 as our threshold (adjustable based on your environment)
"""

import cv2
import numpy as np
import os


class FaceRecognizer:
    """
    Real-time face recognizer using trained LBPH model.
    
    Loads the pre-trained model and ID mapping, then provides
    methods for frame-by-frame face recognition.
    """

    # ----------------------------------------------------------------
    # Confidence threshold for LBPH recognition.
    # Distance < CONFIDENCE_THRESHOLD → known face
    # Distance >= CONFIDENCE_THRESHOLD → unknown face
    # Lower thresholds = stricter matching (fewer false positives)
    # Higher thresholds = more lenient (may accept wrong matches)
    # ----------------------------------------------------------------
    CONFIDENCE_THRESHOLD = 70

    def __init__(self, model_dir="model"):
        """
        Initialize the recognizer with model paths.
        
        Args:
            model_dir (str): Directory containing the trained model
        """
        self.project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.model_dir = os.path.join(self.project_root, model_dir)
        self.model_path = os.path.join(self.model_dir, "trained_model.yml")
        
        # Haar Cascade for face detection
        cascade_path = os.path.join(self.project_root, "data", "haarcascade_frontalface_default.xml")
        if not os.path.exists(cascade_path):
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        
        self.face_cascade = cv2.CascadeClassifier(cascade_path)
        self.recognizer = None
        self.id_map = {}
        self.is_loaded = False

    def load_model(self):
        """
        Load the trained LBPH model and ID mapping.
        
        The model file (.yml) contains:
        - LBP histograms for each training sample
        - Associated integer labels
        - LBPH parameters used during training
        
        Returns:
            bool: True if model loaded successfully
        """
        if not os.path.exists(self.model_path):
            print("[RECOGNIZER ERROR] Trained model not found. Please train first.")
            return False

        try:
            # Create LBPH recognizer and load from saved file
            self.recognizer = cv2.face.LBPHFaceRecognizer_create()
            self.recognizer.read(self.model_path)

            # Load integer label → student_id mapping
            self._load_id_map()
            self.is_loaded = True
            return True

        except Exception as e:
            print(f"[RECOGNIZER ERROR] Failed to load model: {e}")
            return False

    def _load_id_map(self):
        """Load the label-to-student-ID mapping from file."""
        map_path = os.path.join(self.model_dir, "id_map.txt")
        self.id_map = {}
        
        if os.path.exists(map_path):
            with open(map_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if "," in line:
                        parts = line.split(",", 1)
                        self.id_map[int(parts[0])] = parts[1]

    def recognize_frame(self, frame):
        """
        Process a single video frame for face recognition.
        
        Steps:
        1. Convert frame to grayscale
        2. Detect all faces in the frame
        3. For each face, predict using LBPH model
        4. Return list of recognition results
        
        Args:
            frame (numpy.ndarray): BGR color frame from webcam
        
        Returns:
            list: List of dicts with keys:
                - 'student_id': Predicted student ID (or "Unknown")
                - 'confidence': Distance score (lower = better)
                - 'bbox': (x, y, w, h) bounding box of the face
        """
        if not self.is_loaded:
            return []

        results = []
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        faces = self.face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.3, # Faster detection (more skips in scale)
            minNeighbors=6,   # Slightly higher to reduce false positives
            minSize=(40, 40)   # Adjusted for 0.33x downscaled frame
        )

        for (x, y, w, h) in faces:
            # Crop and resize face to match training image size
            face_roi = gray[y:y+h, x:x+w]
            face_resized = cv2.resize(face_roi, (200, 200))

            # ----------------------------------------------------------------
            # LBPH Prediction:
            # ----------------------------------------------------------------
            try:
                label, confidence = self.recognizer.predict(face_resized)
                
                if confidence < self.CONFIDENCE_THRESHOLD:
                    # Known face - map integer label back to student_id
                    student_id = self.id_map.get(label, "Unknown")
                else:
                    # Unknown face - confidence too low (distance too high)
                    student_id = "Unknown"
            except Exception as e:
                print(f"[RECOGNIZER ERROR] Predict failed: {e}")
                student_id = "Unknown"
                confidence = 100.0

            results.append({
                "student_id": student_id,
                "confidence": round(confidence, 2),
                "bbox": (x, y, w, h)
            })

        return results

    def draw_results(self, frame, results, db=None, selected_dep="All"):
        """
        Draw recognition results with department validation.
        """
        for result in results:
            x, y, w, h = result["bbox"]
            student_id = result["student_id"]
            confidence = result["confidence"]
            
            name = student_id
            status_text = ""
            color = (0, 0, 255) # Default Red

            if student_id != "Unknown":
                color = (0, 255, 0) # Default Green
                if db:
                    # Uses the cached get_student method we added
                    student = db.get_student(student_id)
                    if student:
                        name = student.get("name", student_id)
                        student_dep = student.get("dep", "")
                        
                        # Department Validation
                        if selected_dep != "All" and student_dep != selected_dep:
                            color = (0, 165, 255) # Orange for mismatch
                            status_text = "WRONG DEPT"
                
                conf_pct = max(0, min(100, int(100 - confidence)))
                label = f"{name} ({conf_pct}%)"
            else:
                label = "Unknown"

            # Draw
            cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)
            
            # Label
            y_offset = y - 10
            if status_text:
                cv2.putText(frame, status_text, (x, y_offset - 25), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            
            cv2.putText(frame, label, (x, y_offset), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

        return frame
