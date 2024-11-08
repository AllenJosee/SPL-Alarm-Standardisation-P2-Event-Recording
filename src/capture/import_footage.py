import cv2


# Open video file or capturing device (0 for the first connected camera)
cap = cv2.VideoCapture("../../data/raw/sample_shopfloor_footage_1.mp4")
# For capturing from a webcam, you can use cap = cv2.VideoCapture(0)

# Check if the video opened successfully
if not cap.isOpened():
    print("Error: Could not open video.")
    exit()

# Read and display frames in a loop
while cap.isOpened():
    ret, frame = cap.read()  # Read a new frame from the video
    if not ret:
        print("End of video or can't read the frame.")
        break

    # Display the frame
    cv2.imshow("Video", frame)

    # Press 'q' to exit video display early
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

# Release video capture and close windows
cap.release()
cv2.destroyAllWindows()
