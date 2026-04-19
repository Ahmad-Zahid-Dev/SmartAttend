import cv2
print("Probing camera index 0...")
cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
if not cap.isOpened():
    print("Index 0 with DSHOW failed. Trying index 0 Default...")
    cap = cv2.VideoCapture(0)

if cap.isOpened():
    ret, frame = cap.read()
    if ret:
        print(f"SUCCESS! Frame received. Shape: {frame.shape}")
        # Save a test frame to see if it's black
        cv2.imwrite("test_camera.jpg", frame)
    else:
        print("CAP OPENED but READ FAILED.")
    cap.release()
else:
    print("COULD NOT OPEN CAMERA AT ALL.")
