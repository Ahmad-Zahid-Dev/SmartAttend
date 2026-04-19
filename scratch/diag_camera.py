import cv2
import time

def test_camera():
    print("Testing Camera Indices...")
    for i in range(3):
        print(f"Checking index {i}...")
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            print(f"  [OK] Index {i} opened.")
            ret, frame = cap.read()
            if ret:
                print(f"  [OK] Index {i} captured a frame of size {frame.shape}")
                cv2.imwrite(f"test_camera_{i}.jpg", frame)
            else:
                print(f"  [ERROR] Index {i} opened but failed to read frame.")
            cap.release()
        else:
            print(f"  [FAIL] Index {i} could not be opened.")

if __name__ == "__main__":
    test_camera()
