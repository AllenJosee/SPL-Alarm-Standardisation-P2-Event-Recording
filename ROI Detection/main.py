import cv2
import numpy as np
import time # For the time interval

# --- Configuration ---
VIDEO_PATH = 'WIN_20250520_13_53_36_Pro.mp4' # Your video file

# --- ROI Definitions ---
# 1. DETECTION_ROI: Where we look for the moving arm's contour
# This should encompass the entire path of the single moving arm.
DETECTION_ROI_X = 780  # TUNE: Top-left X of the area where the arm moves
DETECTION_ROI_Y = 50   # TUNE: Top-left Y
DETECTION_ROI_W = 200  # TUNE: Width of the arm's movement area
DETECTION_ROI_H = 1000  # TUNE: Height of the arm's movement area

# 2. TOP_ZONE_ROI: When the arm enters this zone, it's considered "OPEN"
# These coordinates are RELATIVE TO THE FULL FRAME
TOP_ZONE_ROI_X = 800  # TUNE
TOP_ZONE_ROI_Y = 100  # TUNE
TOP_ZONE_ROI_W = 150  # TUNE
TOP_ZONE_ROI_H = 200  # TUNE

# 3. BOTTOM_ZONE_ROI: When the arm enters this zone, it's considered "CLOSE"
# These coordinates are RELATIVE TO THE FULL FRAME
BOTTOM_ZONE_ROI_X = 800 # TUNE
BOTTOM_ZONE_ROI_Y = 750 # TUNE
BOTTOM_ZONE_ROI_W = 150 # TUNE
BOTTOM_ZONE_ROI_H = 200 # TUNE

# --- Detection Parameters ---
MIN_CONTOUR_AREA_ARM = 1000 # Min area for the detected moving arm within DETECTION_ROI (TUNE)
DETECTION_INTERVAL_SECONDS = 0 # Cooldown period between detections

# --- Background Subtractor Parameters ---
BG_HISTORY = 150
BG_VAR_THRESHOLD = 75   # (TUNE)

# --- Morphological Operations ---
ERODE_KERNEL_SIZE = (5,5)
ERODE_ITERATIONS = 1
DILATE_KERNEL_SIZE = (7,7) # Slightly more dilation to make arm solid
DILATE_ITERATIONS = 2

# --- Gaussian Blur for Pre-processing (Optional) ---
PRE_BLUR_KERNEL_SIZE = (3,3) # (0,0) or (1,1) to disable

# --- Display Configuration ---
DISPLAY_WIDTH = 1280

# --- Initialization ---
cap = cv2.VideoCapture(VIDEO_PATH)
if not cap.isOpened():
    print(f"Error: Could not open video {VIDEO_PATH}")
    exit()

# Only one background subtractor needed now, for the DETECTION_ROI
bg_subtractor = cv2.createBackgroundSubtractorMOG2(history=BG_HISTORY, varThreshold=BG_VAR_THRESHOLD, detectShadows=False)

# State and Counters
STATE_NEUTRAL = 0
STATE_IN_TOP_ZONE = 1
STATE_IN_BOTTOM_ZONE = 2
current_arm_state = STATE_NEUTRAL # Tracks if arm is currently considered in a zone for counting once

open_count = 0
close_count = 0
last_detection_time = 0 # Timestamp of the last registered open or close event

erode_kernel = np.ones(ERODE_KERNEL_SIZE, np.uint8)
dilate_kernel = np.ones(DILATE_KERNEL_SIZE, np.uint8)

# --- Main Loop ---
while True:
    ret, frame = cap.read()
    if not ret:
        print("End of video or error reading frame.")
        break

    current_time = time.time() # Get current time for interval check
    original_frame_height, original_frame_width = frame.shape[:2]

    # --- 1. Extract DETECTION_ROI for finding the moving arm ---
    detection_roi_actual_y_start = max(0, DETECTION_ROI_Y)
    detection_roi_actual_y_end = min(DETECTION_ROI_Y + DETECTION_ROI_H, original_frame_height)
    detection_roi_actual_x_start = max(0, DETECTION_ROI_X)
    detection_roi_actual_x_end = min(DETECTION_ROI_X + DETECTION_ROI_W, original_frame_width)
    detection_roi_frame = frame[detection_roi_actual_y_start:detection_roi_actual_y_end,
                                detection_roi_actual_x_start:detection_roi_actual_x_end]

    detected_arm_center_full_frame = None # Store (x, y) of detected arm's center in full frame coords

    if detection_roi_frame.size > 0:
        # Pre-process the detection_roi_frame
        processed_detection_roi = detection_roi_frame.copy()
        if PRE_BLUR_KERNEL_SIZE[0] > 1 and PRE_BLUR_KERNEL_SIZE[1] > 1:
            processed_detection_roi = cv2.GaussianBlur(processed_detection_roi, PRE_BLUR_KERNEL_SIZE, 0)

        # Apply background subtraction
        fg_mask_detection_roi = bg_subtractor.apply(processed_detection_roi)

        # Morphology
        fg_mask_detection_roi = cv2.erode(fg_mask_detection_roi, erode_kernel, iterations=ERODE_ITERATIONS)
        fg_mask_detection_roi = cv2.dilate(fg_mask_detection_roi, dilate_kernel, iterations=DILATE_ITERATIONS)
        _, fg_mask_detection_roi = cv2.threshold(fg_mask_detection_roi, 200, 255, cv2.THRESH_BINARY)

        # Find contours in the DETECTION_ROI
        contours, _ = cv2.findContours(fg_mask_detection_roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if contours:
            valid_contours = [c for c in contours if cv2.contourArea(c) > MIN_CONTOUR_AREA_ARM]
            if valid_contours:
                # Assume the largest valid contour is the arm
                arm_contour = max(valid_contours, key=cv2.contourArea)
                x_r, y_r, w_r, h_r = cv2.boundingRect(arm_contour) # Coords relative to detection_roi_frame

                # Calculate center of the arm in FULL FRAME coordinates
                center_x_in_detection_roi = x_r + w_r // 2
                center_y_in_detection_roi = y_r + h_r // 2
                
                center_x_full = center_x_in_detection_roi + detection_roi_actual_x_start
                center_y_full = center_y_in_detection_roi + detection_roi_actual_y_start
                detected_arm_center_full_frame = (center_x_full, center_y_full)

                # Draw the detected arm's bounding box (optional, but good for debug)
                # Convert rect to full frame coords for drawing
                arm_rect_full_x = x_r + detection_roi_actual_x_start
                arm_rect_full_y = y_r + detection_roi_actual_y_start
                cv2.rectangle(frame, (arm_rect_full_x, arm_rect_full_y),
                              (arm_rect_full_x + w_r, arm_rect_full_y + h_r), (0, 255, 0), 2)
                cv2.putText(frame, "Arm", (arm_rect_full_x, arm_rect_full_y - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                cv2.imshow('Detection ROI Mask', fg_mask_detection_roi) # Debug mask


    # --- 2. Check if detected arm is in TOP or BOTTOM zone and handle interval ---
    arm_in_top_zone_now = False
    arm_in_bottom_zone_now = False

    if detected_arm_center_full_frame:
        arm_cx, arm_cy = detected_arm_center_full_frame

        # Check for TOP ZONE entry
        if (TOP_ZONE_ROI_X <= arm_cx < TOP_ZONE_ROI_X + TOP_ZONE_ROI_W and
            TOP_ZONE_ROI_Y <= arm_cy < TOP_ZONE_ROI_Y + TOP_ZONE_ROI_H):
            arm_in_top_zone_now = True
            if current_time - last_detection_time > DETECTION_INTERVAL_SECONDS:
                if current_arm_state != STATE_IN_TOP_ZONE : # Count only on first entry into state after interval
                    open_count += 1
                    print(f"EVENT: Arm entered TOP zone (Open). Count: {open_count}")
                    last_detection_time = current_time
                    current_arm_state = STATE_IN_TOP_ZONE


        # Check for BOTTOM ZONE entry (only if not already counted for top)
        # The 'elif' implies that if it's in both (unlikely with good zone defs), top takes precedence.
        # Or, remove elif if you want to check bottom independently, but cooldown is global.
        elif (BOTTOM_ZONE_ROI_X <= arm_cx < BOTTOM_ZONE_ROI_X + BOTTOM_ZONE_ROI_W and
              BOTTOM_ZONE_ROI_Y <= arm_cy < BOTTOM_ZONE_ROI_Y + BOTTOM_ZONE_ROI_H):
            arm_in_bottom_zone_now = True
            if current_time - last_detection_time > DETECTION_INTERVAL_SECONDS:
                if current_arm_state != STATE_IN_BOTTOM_ZONE:
                    close_count += 1
                    print(f"EVENT: Arm entered BOTTOM zone (Close). Count: {close_count}")
                    last_detection_time = current_time
                    current_arm_state = STATE_IN_BOTTOM_ZONE
    
    # If arm is not in any specific zone currently, reset its counting state (but not the global cooldown time)
    if not arm_in_top_zone_now and not arm_in_bottom_zone_now:
        current_arm_state = STATE_NEUTRAL


    # --- Display Info and ROIs on Main Frame ---
    # Draw DETECTION_ROI (e.g., yellow)
    cv2.rectangle(frame, (detection_roi_actual_x_start, detection_roi_actual_y_start),
                  (detection_roi_actual_x_end, detection_roi_actual_y_end), (0, 255, 255), 1)
    cv2.putText(frame, "Detection Zone", (detection_roi_actual_x_start, detection_roi_actual_y_start - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)

    # Draw TOP_ZONE_ROI (e.g., blue)
    cv2.rectangle(frame, (TOP_ZONE_ROI_X, TOP_ZONE_ROI_Y),
                  (TOP_ZONE_ROI_X + TOP_ZONE_ROI_W, TOP_ZONE_ROI_Y + TOP_ZONE_ROI_H), (255, 0, 0), 2)
    cv2.putText(frame, "Top Zone (Open)", (TOP_ZONE_ROI_X, TOP_ZONE_ROI_Y - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 0), 1)

    # Draw BOTTOM_ZONE_ROI (e.g., red)
    cv2.rectangle(frame, (BOTTOM_ZONE_ROI_X, BOTTOM_ZONE_ROI_Y),
                  (BOTTOM_ZONE_ROI_X + BOTTOM_ZONE_ROI_W, BOTTOM_ZONE_ROI_Y + BOTTOM_ZONE_ROI_H), (0, 0, 255), 2)
    cv2.putText(frame, "Bottom Zone (Close)", (BOTTOM_ZONE_ROI_X, BOTTOM_ZONE_ROI_Y - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)

    # Display Counts
    cv2.putText(frame, f"Open Count : {open_count}", (1400, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 100, 0), 2)
    cv2.putText(frame, f"Close Count : {close_count}", (1400, 70),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 100, 255), 2)
    
    # Display time until next detection possible
    '''time_since_last_detection = current_time - last_detection_time
    cooldown_remaining = max(0, DETECTION_INTERVAL_SECONDS - time_since_last_detection)
    cv2.putText(frame, f"Cooldown: {cooldown_remaining:.1f}s", (20, 90),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (128, 128, 128), 2)'''


    # --- Prepare Frame for Display ---
    display_frame = frame.copy()
    if original_frame_width > DISPLAY_WIDTH:
        aspect_ratio = float(original_frame_height) / original_frame_width
        display_height = int(DISPLAY_WIDTH * aspect_ratio)
        display_frame = cv2.resize(display_frame, (DISPLAY_WIDTH, display_height))
    cv2.imshow('Arm Zone Detection', display_frame)


    if cv2.waitKey(30) & 0xFF == ord('q'):
        break

# --- Cleanup ---
cap.release()
cv2.destroyAllWindows()