"""
LBPH Face Trainer Module
=========================
Trains a Local Binary Patterns Histograms (LBPH) face recognizer using 
captured face images labeled with student IDs.

LBPH Algorithm Explained:
--------------------------
LBPH stands for Local Binary Patterns Histograms. It works in 4 steps:

1. LOCAL BINARY PATTERN COMPUTATION:
   - For each pixel in the face image, compare it with its 8 neighbors
   - If a neighbor's intensity >= center pixel, write "1", else write "0"
   - This creates an 8-bit binary number for each pixel
   - Example: if center pixel = 120 and neighbors are [130, 100, 90, 140, 110, 80, 150, 125]
     → Binary pattern: 1,0,0,1,0,0,1,1 → LBP value = 0b10010011 = 147

2. HISTOGRAM CREATION:
   - Divide the face image into a grid of cells (e.g., 8x8 grid)
   - For each cell, compute a histogram of LBP values (256 bins for 8-bit LBP)
   - Concatenate all cell histograms into a single feature vector

3. TRAINING:
   - For each person (student), compute the LBP histogram from their training images
   - Store these histograms as the "model" for that person

4. RECOGNITION:
   - Compute LBP histogram for the unknown face
   - Compare with all stored histograms using a distance metric (Chi-Square or similar)
   - The person with the closest (smallest distance) histogram is the prediction
   - The distance value serves as a "confidence" score (lower = more confident)

Why LBPH for this project:
- Works well with small training sets (100 images per person is sufficient)
- Robust to monotonic grayscale transformations (handles lighting changes)
- Relatively fast to train and predict (suitable for real-time applications)
- Doesn't require aligned/normalized faces (tolerates slight pose variations)
"""

import cv2
import numpy as np
import os


class FaceTrainer:
    """
    Trains LBPH face recognizer model from captured face images.
    
    The LBPH recognizer in OpenCV uses these parameters:
    - radius (int): Radius of the circular LBP pattern (default: 1)
    - neighbors (int): Number of sample points around center pixel (default: 8)
    - grid_x (int): Number of horizontal cells in the grid (default: 8)
    - grid_y (int): Number of vertical cells in the grid (default: 8)
    - threshold (float): Distance threshold for unknown faces (default: DBL_MAX)
    """

    def __init__(self, data_dir="data/training_images", model_dir="model"):
        """
        Initialize the trainer with directory paths.
        
        Args:
            data_dir (str): Directory containing training images
            model_dir (str): Directory to save the trained model
        """
        self.project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.data_dir = os.path.join(self.project_root, data_dir)
        self.model_dir = os.path.join(self.project_root, model_dir)
        self.model_path = os.path.join(self.model_dir, "trained_model.yml")

        # Create model directory if it doesn't exist
        os.makedirs(self.model_dir, exist_ok=True)

    def get_images_and_labels(self, progress_callback=None):
        """
        Load all face images and extract their corresponding student ID labels.
        
        Image naming convention: StudentID_SampleNumber.jpg
        The StudentID part is extracted and used as the integer label for training.
        
        We need to convert string student IDs to integer labels because 
        OpenCV's LBPH recognizer requires integer labels. We maintain a 
        mapping between integer labels and string student IDs.
        
        Args:
            progress_callback (callable): Optional callback(current, total) for progress updates
        
        Returns:
            tuple: (face_samples, labels, id_map)
                - face_samples: list of numpy arrays (grayscale face images)
                - labels: list of integer labels corresponding to student IDs
                - id_map: dict mapping integer labels to string student IDs
        """
        face_samples = []
        labels = []
        id_map = {}  # Maps integer label → string student_id
        label_counter = 0

        if not os.path.exists(self.data_dir):
            print("[TRAINER WARNING] Training images directory not found")
            return face_samples, labels, id_map

        # Support nested structure
        image_paths = []
        for root, dirs, files in os.walk(self.data_dir):
            for f in files:
                if f.endswith(".jpg"):
                    image_paths.append(os.path.join(root, f))
        
        total = len(image_paths)

        for idx, img_path in enumerate(image_paths):
            filename = os.path.basename(img_path)
            # Extract student_id from filename (format: StudentID_SampleNumber.jpg)
            parts = filename.rsplit("_", 1)
            if len(parts) != 2:
                continue

            student_id = parts[0]  # e.g., "STU001"

            # Assign integer label to each unique student_id
            if student_id not in id_map.values():
                # Check if this student_id already has a label
                existing_label = None
                for lbl, sid in id_map.items():
                    if sid == student_id:
                        existing_label = lbl
                        break
                
                if existing_label is None:
                    id_map[label_counter] = student_id
                    label_counter += 1

            # Find the integer label for this student_id
            int_label = None
            for lbl, sid in id_map.items():
                if sid == student_id:
                    int_label = lbl
                    break

            if int_label is None:
                continue

            # Load the grayscale face image
            face_img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)

            if face_img is not None:
                face_samples.append(np.array(face_img, dtype=np.uint8))
                labels.append(int_label)

            # Report progress via callback
            if progress_callback:
                progress_callback(idx + 1, total)

        return face_samples, labels, id_map

    def train(self, progress_callback=None):
        """
        Train the LBPH face recognizer with all captured face images.
        
        Training process:
        1. Load all images and extract labels (get_images_and_labels)
        2. Create LBPH face recognizer with specified parameters
        3. Call recognizer.train(faces, labels) which:
           - Computes LBP for each face image
           - Divides each LBP image into grid cells
           - Builds histograms for each cell
           - Stores concatenated histograms per label (student)
        4. Save the trained model to a YAML file
        
        Args:
            progress_callback (callable): Optional callback for progress updates
        
        Returns:
            tuple: (success: bool, id_map: dict, total_images: int)
        """
        # Step 1: Load images and labels
        faces, labels, id_map = self.get_images_and_labels(progress_callback)

        if len(faces) == 0:
            return False, {}, 0

        # Step 2: Create LBPH recognizer
        # ----------------------------------------------------------------
        # LBPH Parameters:
        # - radius=1: Use immediate 8-pixel neighborhood
        # - neighbors=8: Sample 8 points around each center pixel
        # - grid_x=8, grid_y=8: Divide face into 8x8 = 64 cells
        #   More cells = more spatial information but slower
        # ----------------------------------------------------------------
        recognizer = cv2.face.LBPHFaceRecognizer_create(
            radius=1,
            neighbors=8,
            grid_x=8,
            grid_y=8
        )

        # Step 3: Train the recognizer
        # This computes LBP histograms for all face images and stores them
        labels_np = np.array(labels)
        recognizer.train(faces, labels_np)

        # Step 4: Save trained model and ID mapping
        recognizer.save(self.model_path)
        
        # Save the ID mapping (integer label → student_id) for recognition
        self._save_id_map(id_map)

        return True, id_map, len(faces)

    def _save_id_map(self, id_map):
        """
        Save the label-to-student-ID mapping to a text file.
        This mapping is needed during recognition to convert integer 
        predictions back to meaningful student IDs.
        """
        map_path = os.path.join(self.model_dir, "id_map.txt")
        with open(map_path, "w") as f:
            for label, student_id in id_map.items():
                f.write(f"{label},{student_id}\n")

    def load_id_map(self):
        """
        Load the label-to-student-ID mapping from file.
        
        Returns:
            dict: Maps integer labels to string student IDs
        """
        map_path = os.path.join(self.model_dir, "id_map.txt")
        id_map = {}
        
        if os.path.exists(map_path):
            with open(map_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if "," in line:
                        parts = line.split(",", 1)
                        id_map[int(parts[0])] = parts[1]
        
        return id_map

    def is_model_trained(self):
        """Check if a trained model file exists."""
        return os.path.exists(self.model_path)

    def get_training_stats(self):
        """
        Get statistics about the training data.
        
        Returns:
            dict: Stats including total images, unique students, etc.
        """
        if not os.path.exists(self.data_dir):
            return {"total_images": 0, "unique_students": 0, "students": []}

        student_counts = {}
        for filename in os.listdir(self.data_dir):
            if filename.endswith(".jpg"):
                parts = filename.rsplit("_", 1)
                if len(parts) == 2:
                    student_id = parts[0]
                    student_counts[student_id] = student_counts.get(student_id, 0) + 1

        return {
            "total_images": sum(student_counts.values()),
            "unique_students": len(student_counts),
            "students": student_counts
        }
