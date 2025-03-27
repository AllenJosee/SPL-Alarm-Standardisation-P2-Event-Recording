# video_recorder.py

import cv2
import time


def record_video(
    output_filename="output_clip.mp4", duration=0, source=0, setRecordstop=False
):
    # Open video capture (0 for the default camera, or specify a video file path)
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print("Error: Could not open video capture.")
        return

    # Set up the video writer (to save the recorded clip)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # Codec for .mp4
    output = cv2.VideoWriter(
        output_filename, fourcc, 20.0, (int(cap.get(3)), int(cap.get(4)))
    )

    # Capture start time
    start_time = time.time()

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            print("Error: Failed to capture frame.")
            break

        # Write the frame to the output video
        output.write(frame)

        # Display the frame (optional, you can skip this if you don't need to preview)
        cv2.imshow("Recording", frame)

        # Stop capturing after the specified duration
        if ((time.time() - start_time) > duration) or (setRecordstop):
            print("Recording complete.")
            break

        # Press 'q' to stop early
        if cv2.waitKey(1) & 0xFF == ord("q"):
            print("Recording stopped by user.")
            break

    # Release everything
    cap.release()
    output.release()
    cv2.destroyAllWindows()
