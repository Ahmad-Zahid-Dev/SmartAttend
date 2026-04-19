"""
Face Capture Module
===================
Captures face images from the webcam for a specific student.
Uses OpenCV's Haar Cascade Classifier for face detection.

How it works:
1. Opens the webcam and reads frames in real-time
2. Converts each frame to grayscale (required for Haar Cascade)
3. Detects faces using the Haar Cascade frontal face detector
4. Crops detected faces, resizes to a standard size (200x200)
5. Saves each face image with the naming convention: StudentID_SampleNumber.jpg
6. Captures 100 sample images per student for robust training
"""

import cv2
import numpy as np
import os


class FaceCapture:
    """
    Handles webcam face capture for student registration.
    
    The Haar Cascade method works by:
    - Scanning the image at multiple scales using a sliding window
    - At each position, evaluating Haar-like features (edge, line, center-surround)
    - Using a cascade of classifiers (stages) - early stages quickly reject non-face regions
    - Only regions passing ALL stages are classified as faces
    """

    def __init__(self, data_dir="data/training_images"):
        """
        Initialize face capture with the Haar Cascade classifier.
        
        Args:
            data_dir (str): Directory to store captured face images
        """
        self.data_dir = data_dir
        
        # Get the absolute path to the project root
        self.project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        # Path to Haar Cascade XML file for frontal face detection
        # This pre-trained model comes with OpenCV and detects frontal faces
        cascade_path = os.path.join(self.project_root, "data", "haarcascade_frontalface_default.xml")
        
        # Fallback to OpenCV's built-in cascade if local copy not found
        if not os.path.exists(cascade_path):
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        
        self.face_cascade = cv2.CascadeClassifier(cascade_path)
        
        # Ensure the training images directory exists
        abs_data_dir = os.path.join(self.project_root, data_dir)
        os.makedirs(abs_data_dir, exist_ok=True)

    def get_abs_data_dir(self):
        """Get the absolute path to training images directory."""
        return os.path.join(self.project_root, self.data_dir)

    def capture_faces(self, student_id, num_samples=100, camera_index=0, callback=None):
        """
        Capture face images from webcam for a given student.
        
        Process:
        1. Open webcam → read frame
        2. Convert to grayscale (Haar Cascade operates on intensity, not color)
        3. detectMultiScale() scans image at multiple scales to find faces
        4. For each detected face: crop, resize to 200x200, save as grayscale
        5. Repeat until num_samples images are captured
        
        Args:
            student_id (str): Student ID used for naming images (e.g., "STU001")
            num_samples (int): Number of face samples to capture (default: 100)
            camera_index (int): Webcam device index (0 = default camera)
            callback (callable): Optional callback function(frame, count, total) 
                                 for UI updates. If provided, this function runs 
                                 in a non-blocking manner for Tkinter integration.
        
        Returns:
            int: Number of images successfully captured
        """
        cap = cv2.VideoCapture(camera_index)
        
        if not cap.isOpened():
            print("[CAPTURE ERROR] Cannot open webcam")
            return 0

        # Set camera resolution for better quality
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        count = 0
        abs_data_dir = self.get_abs_data_dir()

        while count < num_samples:
            ret, frame = cap.read()
            if not ret:
                break

            # Convert to grayscale - Haar Cascade works on single-channel images
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # ----------------------------------------------------------------
            # detectMultiScale parameters:
            # - scaleFactor (1.2): Image is scaled down by 20% at each level.
            #   Smaller values = more accurate but slower.
            # - minNeighbors (5): Minimum number of neighbor rectangles that 
            #   must agree before a region is classified as a face.
            #   Higher values = fewer false positives but may miss faces.
            # - minSize (100,100): Minimum face size to detect (in pixels).
            #   Filters out small, distant faces.
            # ----------------------------------------------------------------
            faces = self.face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.2,
                minNeighbors=5,
                minSize=(100, 100)
            )

            for (x, y, w, h) in faces:
                count += 1
                
                # Crop the detected face region from the grayscale image
                face_roi = gray[y:y+h, x:x+w]
                
                # Resize to standard 200x200 for consistent training input
                face_resized = cv2.resize(face_roi, (200, 200))

                # Save in student-specific subfolder
                student_folder = os.path.join(abs_data_dir, student_id)
                os.makedirs(student_folder, exist_ok=True)
                
                img_path = os.path.join(
                    student_folder, 
                    f"{student_id}_{count}.jpg"
                )
                cv2.imwrite(img_path, face_resized)

                # Draw rectangle around detected face on the color frame
                cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 255), 2)
                
                # Display sample count on the frame
                cv2.putText(
                    frame, f"Sample: {count}/{num_samples}",
                    (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 255, 255), 2
                )

                if count >= num_samples:
                    break

            # If a callback is provided (for Tkinter UI), call it with the frame
            if callback:
                callback(frame, count, num_samples)
            else:
                # Standalone mode: show OpenCV window
                cv2.imshow("Capturing Faces - Press Q to Quit", frame)
                key = cv2.waitKey(100) & 0xFF
                if key == ord('q'):
                    break

        cap.release()
        if not callback:
            cv2.destroyAllWindows()

        return count

    def get_sample_count(self, student_id):
        """
        Count existing face samples for a specific student.
        
        Args:
            student_id (str): Student ID to check
        
        Returns:
            int: Number of existing samples
        """
        abs_data_dir = self.get_abs_data_dir()
        if not os.path.exists(abs_data_dir):
            return 0
        
        count = 0
        for filename in os.listdir(abs_data_dir):
            if filename.startswith(f"{student_id}_") and filename.endswith(".jpg"):
                count += 1
        return count

    def delete_samples(self, student_id):
        """
        Delete the entire image folder for a specific student.
        """
        import shutil
        student_folder = os.path.join(self.get_abs_data_dir(), student_id)
        if os.path.exists(student_folder):
            shutil.rmtree(student_folder)
            print(f"[CAPTURE] Deleted folder: {student_folder}")
        else:
            # Fallback: check if files exist in root (old version compatibility)
            abs_data_dir = self.get_abs_data_dir()
            for filename in os.listdir(abs_data_dir):
                if filename.startswith(f"{student_id}_") and filename.endswith(".jpg"):
                    os.remove(os.path.join(abs_data_dir, filename))
