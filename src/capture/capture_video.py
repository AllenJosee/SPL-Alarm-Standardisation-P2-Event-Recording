import cv2

# Open a connection to the webcam (0 is usually the default camera)
cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("Error: Could not open webcam.")
    exit()

while True:
    # Capture frame-by-frame
    ret, frame = cap.read()

    # Check if frame is captured correctly
    if not ret:
        print("Error: Could not read frame.")
        break

    # Display the resulting frame
    cv2.imshow("Webcam Feed", frame)

    # Break the loop on 'q' key press
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

# Release the capture when done
cap.release()
cv2.destroyAllWindows()
