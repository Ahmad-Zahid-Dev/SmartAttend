import cv2

def check_camera():
    for i in range(5):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ret, frame = cap.read()
            if ret:
                print(f"Camera found at index {i}")
                cap.release()
                return i
            cap.release()
    print("No camera found")
    return None

if __name__ == "__main__":
    check_camera()
