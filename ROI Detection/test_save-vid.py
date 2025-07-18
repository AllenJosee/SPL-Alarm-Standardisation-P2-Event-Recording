#save the output video 


import cv2
import numpy as np
import time # For the time interval
import os # For path manipulation for output video

# --- Configuration ---
VIDEO_PATH = 'WIN_20250520_13_55_10_Pro.mp4' # Your video file
OUTPUT_VIDEO_FILENAME = 'output.mp4' # Name for the saved video

# --- ROI Definitions ---
# 1. DETECTION_ROI: Where we look for the moving arm's contour
DETECTION_ROI_X = 780
DETECTION_ROI_Y = 50
DETECTION_ROI_W = 200
DETECTION_ROI_H = 1000

# 2. TOP_ZONE_ROI: When the arm enters this zone, it's considered "OPEN"
TOP_ZONE_ROI_X = 800
TOP_ZONE_ROI_Y = 100
TOP_ZONE_ROI_W = 150
TOP_ZONE_ROI_H = 200

# 3. BOTTOM_ZONE_ROI: When the arm enters this zone, it's considered "CLOSE"
BOTTOM_ZONE_ROI_X = 800
BOTTOM_ZONE_ROI_Y = 750
BOTTOM_ZONE_ROI_W = 150
BOTTOM_ZONE_ROI_H = 200

# --- Detection Parameters ---
MIN_CONTOUR_AREA_ARM = 1000
DETECTION_INTERVAL_SECONDS = 3.3

# --- Background Subtractor Parameters ---
BG_HISTORY = 150
BG_VAR_THRESHOLD = 75

# --- Morphological Operations ---
ERODE_KERNEL_SIZE = (5,5)
ERODE_ITERATIONS = 1
DILATE_KERNEL_SIZE = (7,7)
DILATE_ITERATIONS = 2

# --- Gaussian Blur for Pre-processing (Optional) ---
PRE_BLUR_KERNEL_SIZE = (3,3)

# --- Display Configuration ---
DISPLAY_WIDTH = 1280 # Width for the live display window

# --- Initialization ---
cap = cv2.VideoCapture(VIDEO_PATH)
if not cap.isOpened():
    print(f"Error: Could not open video {VIDEO_PATH}")
    exit()

# Get original video properties for VideoWriter
original_frame_width_for_writer = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
original_frame_height_for_writer = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
fps = cap.get(cv2.CAP_PROP_FPS)
if fps == 0: # Fallback if FPS is not available
    fps = 30 # Common default

# Define the codec and create VideoWriter object
# Use 'mp4v' for .mp4, or 'XVID' for .avi
# Ensure you have the necessary codecs installed. 'mp4v' is usually good.
fourcc = cv2.VideoWriter_fourcc(*'mp4v')
out_video = cv2.VideoWriter(OUTPUT_VIDEO_FILENAME, fourcc, fps, (original_frame_width_for_writer, original_frame_height_for_writer))
print(f"Output video will be saved to: {os.path.abspath(OUTPUT_VIDEO_FILENAME)}")
print(f"Output dimensions: {original_frame_width_for_writer}x{original_frame_height_for_writer}, FPS: {fps}")


bg_subtractor = cv2.createBackgroundSubtractorMOG2(history=BG_HISTORY, varThreshold=BG_VAR_THRESHOLD, detectShadows=False)

STATE_NEUTRAL = 0
STATE_IN_TOP_ZONE = 1
STATE_IN_BOTTOM_ZONE = 2
current_arm_state = STATE_NEUTRAL

open_count = 0
close_count = 0
last_detection_time = 0

erode_kernel = np.ones(ERODE_KERNEL_SIZE, np.uint8)
dilate_kernel = np.ones(DILATE_KERNEL_SIZE, np.uint8)

frame_count = 0 # For debugging output video issues

# --- Main Loop ---
while True:
    ret, frame_to_process = cap.read() # Read frame for processing
    if not ret:
        print("End of video or error reading frame.")
        break
    
    frame_count += 1
    # Make a copy of the frame to draw on, so the original frame_to_process is clean for potential future use
    # if needed (though here we draw directly on frame_to_process for VideoWriter)
    # frame_for_drawing = frame_to_process.copy()

    current_time = time.time()
    # We use original_frame_width_for_writer and original_frame_height_for_writer for consistency now
    # original_frame_height, original_frame_width = frame_to_process.shape[:2] # Already got these

    # --- 1. Extract DETECTION_ROI for finding the moving arm ---
    detection_roi_actual_y_start = max(0, DETECTION_ROI_Y)
    detection_roi_actual_y_end = min(DETECTION_ROI_Y + DETECTION_ROI_H, original_frame_height_for_writer)
    detection_roi_actual_x_start = max(0, DETECTION_ROI_X)
    detection_roi_actual_x_end = min(DETECTION_ROI_X + DETECTION_ROI_W, original_frame_width_for_writer)
    detection_roi_frame = frame_to_process[detection_roi_actual_y_start:detection_roi_actual_y_end,
                                           detection_roi_actual_x_start:detection_roi_actual_x_end]

    detected_arm_center_full_frame = None
    fg_mask_detection_roi_for_display = None # Initialize for display

    if detection_roi_frame.size > 0:
        processed_detection_roi = detection_roi_frame.copy()
        if PRE_BLUR_KERNEL_SIZE[0] > 1 and PRE_BLUR_KERNEL_SIZE[1] > 1:
            processed_detection_roi = cv2.GaussianBlur(processed_detection_roi, PRE_BLUR_KERNEL_SIZE, 0)

        fg_mask_detection_roi = bg_subtractor.apply(processed_detection_roi)
        fg_mask_detection_roi_for_display = fg_mask_detection_roi.copy() # For display before morphology

        fg_mask_detection_roi = cv2.erode(fg_mask_detection_roi, erode_kernel, iterations=ERODE_ITERATIONS)
        fg_mask_detection_roi = cv2.dilate(fg_mask_detection_roi, dilate_kernel, iterations=DILATE_ITERATIONS)
        _, fg_mask_detection_roi = cv2.threshold(fg_mask_detection_roi, 200, 255, cv2.THRESH_BINARY)
        fg_mask_detection_roi_for_display = fg_mask_detection_roi # Update to show processed mask

        contours, _ = cv2.findContours(fg_mask_detection_roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if contours:
            valid_contours = [c for c in contours if cv2.contourArea(c) > MIN_CONTOUR_AREA_ARM]
            if valid_contours:
                arm_contour = max(valid_contours, key=cv2.contourArea)
                x_r, y_r, w_r, h_r = cv2.boundingRect(arm_contour)

                center_x_in_detection_roi = x_r + w_r // 2
                center_y_in_detection_roi = y_r + h_r // 2
                center_x_full = center_x_in_detection_roi + detection_roi_actual_x_start
                center_y_full = center_y_in_detection_roi + detection_roi_actual_y_start
                detected_arm_center_full_frame = (center_x_full, center_y_full)

                arm_rect_full_x = x_r + detection_roi_actual_x_start
                arm_rect_full_y = y_r + detection_roi_actual_y_start
                cv2.rectangle(frame_to_process, (arm_rect_full_x, arm_rect_full_y),
                              (arm_rect_full_x + w_r, arm_rect_full_y + h_r), (0, 255, 0), 2)
                cv2.putText(frame_to_process, "Arm", (arm_rect_full_x, arm_rect_full_y - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

    if fg_mask_detection_roi_for_display is not None:
         cv2.imshow('Detection ROI Mask', fg_mask_detection_roi_for_display)


    # --- 2. Check if detected arm is in TOP or BOTTOM zone and handle interval ---
    arm_in_top_zone_now = False
    arm_in_bottom_zone_now = False

    if detected_arm_center_full_frame:
        arm_cx, arm_cy = detected_arm_center_full_frame
        if (TOP_ZONE_ROI_X <= arm_cx < TOP_ZONE_ROI_X + TOP_ZONE_ROI_W and
            TOP_ZONE_ROI_Y <= arm_cy < TOP_ZONE_ROI_Y + TOP_ZONE_ROI_H):
            arm_in_top_zone_now = True
            if current_time - last_detection_time > DETECTION_INTERVAL_SECONDS:
                if current_arm_state != STATE_IN_TOP_ZONE:
                    open_count += 1
                    print(f"EVENT: Arm entered TOP zone (Open). Count: {open_count}")
                    last_detection_time = current_time
                    current_arm_state = STATE_IN_TOP_ZONE
        elif (BOTTOM_ZONE_ROI_X <= arm_cx < BOTTOM_ZONE_ROI_X + BOTTOM_ZONE_ROI_W and
              BOTTOM_ZONE_ROI_Y <= arm_cy < BOTTOM_ZONE_ROI_Y + BOTTOM_ZONE_ROI_H):
            arm_in_bottom_zone_now = True
            if current_time - last_detection_time > DETECTION_INTERVAL_SECONDS:
                if current_arm_state != STATE_IN_BOTTOM_ZONE:
                    close_count += 1
                    print(f"EVENT: Arm entered BOTTOM zone (Close). Count: {close_count}")
                    last_detection_time = current_time
                    current_arm_state = STATE_IN_BOTTOM_ZONE
    
    if not arm_in_top_zone_now and not arm_in_bottom_zone_now:
        current_arm_state = STATE_NEUTRAL

    # --- Display Info and ROIs on Main Frame (this frame will be written to video) ---
    cv2.rectangle(frame_to_process, (detection_roi_actual_x_start, detection_roi_actual_y_start),
                  (detection_roi_actual_x_end, detection_roi_actual_y_end), (0, 255, 255), 1)
    cv2.putText(frame_to_process, "Detection Zone", (detection_roi_actual_x_start, detection_roi_actual_y_start - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
    cv2.rectangle(frame_to_process, (TOP_ZONE_ROI_X, TOP_ZONE_ROI_Y),
                  (TOP_ZONE_ROI_X + TOP_ZONE_ROI_W, TOP_ZONE_ROI_Y + TOP_ZONE_ROI_H), (255, 0, 0), 2)
    cv2.putText(frame_to_process, "Top Zone (Open)", (TOP_ZONE_ROI_X, TOP_ZONE_ROI_Y - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 0), 1)
    cv2.rectangle(frame_to_process, (BOTTOM_ZONE_ROI_X, BOTTOM_ZONE_ROI_Y),
                  (BOTTOM_ZONE_ROI_X + BOTTOM_ZONE_ROI_W, BOTTOM_ZONE_ROI_Y + BOTTOM_ZONE_ROI_H), (0, 0, 255), 2)
    cv2.putText(frame_to_process, "Bottom Zone (Close)", (BOTTOM_ZONE_ROI_X, BOTTOM_ZONE_ROI_Y - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
    cv2.putText(frame_to_process, f"Open Count : {open_count}", (1400, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 100, 0), 2)
    cv2.putText(frame_to_process, f"Close Count : {close_count}", (1400, 70),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 100, 255), 2)
    
    # --- Write the frame with all drawings to the output video ---
    if out_video.isOpened():
        out_video.write(frame_to_process)
    else:
        if frame_count == 1: # Print error only once
             print("Error: VideoWriter is not open. Cannot write frame.")


    # --- Prepare Frame for Live Display (can be resized) ---
    display_frame_live = frame_to_process.copy() # Use the already processed frame for live display
    if original_frame_width_for_writer > DISPLAY_WIDTH:
        aspect_ratio_live = float(original_frame_height_for_writer) / original_frame_width_for_writer
        display_height_live = int(DISPLAY_WIDTH * aspect_ratio_live)
        display_frame_live = cv2.resize(display_frame_live, (DISPLAY_WIDTH, display_height_live))
    cv2.imshow('Arm Zone Detection', display_frame_live)

    if cv2.waitKey(30) & 0xFF == ord('q'):
        break

# --- Cleanup ---
cap.release()
if out_video.isOpened(): # Release VideoWriter if it was successfully opened
    out_video.release()
    print(f"Output video saved: {OUTPUT_VIDEO_FILENAME}")
else:
    print("Output video was not written due to VideoWriter error.")
cv2.destroyAllWindows()