#object detect on top of live feed

from flask import Flask, render_template, Response, request, redirect, url_for, jsonify, session, send_from_directory, flash
import os
import cv2
import json
from threading import Thread, Event, Lock
import time
import shutil
from functools import wraps
import atexit
from ultralytics import YOLO

recordings_dir = "src/recordings"
incidents_dir = "src/incidents"
settings_file = "src/settings.json"

class Camera:
    #device index=0 -> webcam
    def __init__(self, camera_id, recordings_dir, incidents_dir, settings_file, description="", device_index=0):
        self.camera_id = str(camera_id)
        self.recordings_dir = recordings_dir
        self.incidents_dir = incidents_dir
        self.settings_file = settings_file
        self.description = description 
        self.device_index = device_index
        
        self.capture = None 
        self.capture_lock = Lock() 
        self.active_feed_clients = 0 

        self.recording = False
        self._stop_recording_event = Event()
        self.recording_thread = None

        # --- YOLO Detection Attributes ---
        self.yolo_model = None
        self.yolo_class_names_dict = {} 
        # This assumes 'helmet_detection_runs' is at the same level as your main Flask .py file
        self.yolo_model_path = os.path.abspath(os.path.join(
            'helmet_detection_runs', 'yolov8s_helmet_head_exp17', 'weights', 'best.pt'
        ))
        self.yolo_confidence_threshold = 0.4
        self.yolo_class_colors = {
            'helmet': (0, 255, 0),  # Green
            'head': (0, 0, 255),    # Red
            'UNKNOWN_CLASS': (128, 128, 128) # Grey for unknown
        }
        self.yolo_font_scale = 0.6
        self.yolo_box_thickness = 2
        self.yolo_text_thickness = 2
        
        self.detection_enabled = True # Try to enable by default
        self.yolo_model_loaded_successfully = False

        self.frame_counter_for_detection = 0  # Counts frames since last detection
        self.detection_skip_interval = 1      # Detect every Nth frame
        self.last_known_detections = []

        os.makedirs(recordings_dir, exist_ok=True)
        os.makedirs(incidents_dir, exist_ok=True)
        self.load_settings()

    def _init_yolo_model(self):
        """Initializes the YOLO model. Called lazily."""
        if self.yolo_model_loaded_successfully or not self.detection_enabled:
            return

        absolute_model_path = self.yolo_model_path
        if not os.path.exists(absolute_model_path):
            print(f"ERROR (Camera {self.camera_id}): YOLO Trained model not found at {absolute_model_path}")
            print(f"       Please ensure the model path is correct.")
            print(f"       Helmet/Head detection will be DISABLED for camera {self.camera_id}.")
            self.detection_enabled = False # Disable detection if model file is missing
            return

        try:
            print(f" (Camera {self.camera_id}) Loading YOLO model from {absolute_model_path}...")
            self.yolo_model = YOLO(absolute_model_path)
            self.yolo_class_names_dict = self.yolo_model.names  # Get class names from the model
            self.yolo_model_loaded_successfully = True
            print(f" (Camera {self.camera_id}) YOLO Model loaded successfully. Class Names: {self.yolo_class_names_dict}")
        except Exception as e:
            print(f"ERROR (Camera {self.camera_id}): Error loading YOLO model: {e}")
            print(f"       Helmet/Head detection will be DISABLED for camera {self.camera_id}.")
            self.yolo_model = None
            self.detection_enabled = False # Disable detection on loading error

    
    def _get_or_init_recording_capture(self): 
        if self.recording_capture is None or not self.recording_capture.isOpened(): 
            if self.recording_capture is not None:
                self.recording_capture.release()
                self.recording_capture = None
                print(f"Released stale recording capture for camera {self.camera_id} before re-initializing.")
            
            try:
                print(f"Initializing cv2.VideoCapture for RECORDING - camera {self.camera_id} (device: {self.device_index})...")
                self.recording_capture = cv2.VideoCapture(self.device_index)
                if not self.recording_capture.isOpened():
                    print(f"Error: Could not open video device {self.device_index} for RECORDING on camera {self.camera_id}")
                    self.recording_capture = None
                else:
                    print(f"Successfully opened camera {self.camera_id} for RECORDING.")
            except Exception as e:
                print(f"Exception initializing cv2.VideoCapture for RECORDING on camera {self.camera_id}: {e}")
                self.recording_capture = None
        return self.recording_capture
    
    #New fix 8/5/25
    def _ensure_capture_initialized(self):
        """Initializes or returns the existing capture object. Protected by lock."""
        with self.capture_lock:
            if self.capture is None or not self.capture.isOpened():
                if self.capture is not None: 
                    print(f"Releasing stale capture for camera {self.camera_id} before re-initializing.")
                    self.capture.release()
                    self.capture = None
                try:
                    print(f"Initializing cv2.VideoCapture for camera {self.camera_id} (device: {self.device_index})...")
                    self.capture = cv2.VideoCapture(self.device_index)
                    if not self.capture.isOpened():
                        print(f"Error: Could not open video device {self.device_index} for camera {self.camera_id}")
                        self.capture = None
                    else:
                        print(f"Successfully opened camera {self.camera_id}.")
                except Exception as e:
                    print(f"Exception initializing cv2.VideoCapture for camera {self.camera_id}: {e}")
                    self.capture = None
            return self.capture
        
    def _release_capture_if_unused(self):
        """Releases the capture object ONLY if not recording AND no active feed clients."""
        with self.capture_lock:
            if self.capture is not None and self.capture.isOpened():
                if not self.recording and self.active_feed_clients == 0:
                    print(f"Releasing capture for camera {self.camera_id} (no recording, no feed clients).")
                    self.capture.release()
                    self.capture = None
                else:
                    print(f"Capture for camera {self.camera_id} NOT released. Recording: {self.recording}, Feed Clients: {self.active_feed_clients}")

    def release_capture(self): # Called by atexit or logout for a FULL shutdown of this camera
        print(f"FULL release_capture called for camera {self.camera_id}")
        if self.recording:
            self.stop_recording_logic(called_from_release_all=True) # Will handle thread and flags
        
        # Even if not recording, ensure capture is released
        with self.capture_lock:
            if self.capture is not None and self.capture.isOpened():
                print(f"Force releasing capture for camera {self.camera_id} during full shutdown.")
                self.capture.release()
                self.capture = None
        self.active_feed_clients = 0 # Reset feed clients on full release
        print(f"All resources for camera {self.camera_id} should be released after full shutdown sequence.")


    def to_dict(self):
        return {
            "camera_id": self.camera_id,
            "recordings_dir": self.recordings_dir,
            "incidents_dir": self.incidents_dir,
            "settings_file": self.settings_file,
            "description": self.description 
        }
    
    @staticmethod
    def from_dict(data):
        if 'camera_id' not in data:
            raise ValueError("Camera data dictionary missing 'camera_id'")
        return Camera(
            data["camera_id"],
            data["recordings_dir"],
            data["incidents_dir"],
            data["settings_file"],
            data.get("description", "")
        )

    def load_settings(self):
        if not os.path.exists(self.settings_file):
            default_settings = {
                "max_videos": 5,
                "video_duration": 5,  # seconds
            }
            with open(self.settings_file, "w") as f:
                json.dump(default_settings, f)
        with open(self.settings_file, "r") as f:
            self.settings = json.load(f)

    def load_videos_from_folder(self, folder):
        videos = []
        if not os.path.isdir(folder):
            print(f"  [load_videos_from_folder Camera {self.camera_id}] ERROR: Directory not found: {folder}")
            return [] 

        try:
            all_items_in_dir = os.listdir(folder)
        except Exception as e:
            return [] 

        for file in all_items_in_dir:
            if file.endswith(".mp4"):
                filepath = os.path.join(folder, file)
                try:
                    raw_ts = os.path.getctime(filepath)
                    display_ts = time.ctime(raw_ts)
                    videos.append({
                        "filename": file,
                        "timestamp": display_ts,
                        "raw_timestamp": raw_ts
                    })
                except FileNotFoundError:
                    print(f"      [load_videos_from_folder] ERROR: FileNotFoundError getting timestamp for {filepath}. Skipping.")
                except Exception as e:
                    print(f"      [load_videos_from_folder] ERROR: Exception getting timestamp/appending for {filepath}: {e}. Skipping.")
            else:
                 print(f"      [load_videos_from_folder] Item is NOT an .mp4 file. Skipping.")

        print(f"  [load_videos_from_folder Camera {self.camera_id}] Finished processing. Returning list with {len(videos)} videos.")
        return videos


    def load_incident_videos(self):
        incident_videos = []
        if not os.path.isdir(self.incidents_dir):
             print(f"Warning: Incident directory not found: {self.incidents_dir}")
             return []
        for folder in os.listdir(self.incidents_dir):
            folder_path = os.path.join(self.incidents_dir, folder)
            if os.path.isdir(folder_path):
                for file in os.listdir(folder_path):
                    if file.endswith(".mp4"):
                        file_path = os.path.join(folder_path, file)
                        try: 
                            raw_ts = os.path.getctime(file_path)
                            display_ts = time.ctime(raw_ts)
                            incident_videos.append({
                                "filename": file,
                                "incident_folder": folder,
                                "timestamp": display_ts,     
                                "raw_timestamp": raw_ts     
                            })
                        except FileNotFoundError:
                            print(f"Warning: Incident file not found while loading, skipping: {file_path}")
                        except Exception as e:
                            print(f"Warning: Error getting timestamp for incident file {file_path}, skipping: {e}")
        return incident_videos

    
    def update_settings(self, max_videos, video_duration):
        self.settings["max_videos"] = max_videos
        self.settings["video_duration"] = video_duration
        with open(self.settings_file, "w") as f:
            json.dump(self.settings, f)


    def start_recording_thread(self):
        if self.recording and self.recording_thread and self.recording_thread.is_alive():
            print(f"Camera {self.camera_id} is already recording.")
            return self.recording_thread

        print(f"Attempting to start recording thread for camera {self.camera_id}...")
        if self._ensure_capture_initialized(): # This gets or creates self.capture
            self.recording = True
            self._stop_recording_event.clear()
            
            if self.recording_thread and self.recording_thread.is_alive():
                print(f"Warning: Previous recording thread for {self.camera_id} still alive. Joining...")
                self.recording_thread.join(timeout=0.5)

            self.recording_thread = Thread(target=self.record_video, name=f"RecordThread-{self.camera_id}")
            self.recording_thread.daemon = True
            self.recording_thread.start()
            print(f"Recording thread started for camera {self.camera_id}.")
            return self.recording_thread
        else:
            print(f"Failed to initialize capture for recording on camera {self.camera_id}. Recording not started.")
            self.recording = False
            return None

    def stop_recording_logic(self, called_from_release_all=False):
        print(f"stop_recording_logic called for camera {self.camera_id}. From release_all: {called_from_release_all}")
        if not self.recording and not (self.recording_thread and self.recording_thread.is_alive()):
            print(f"Camera {self.camera_id} not actively recording or thread inactive.")
            if not called_from_release_all:
                self._release_capture_if_unused() # Try to release main capture if no one is using it
            return

        self.recording = False # Signal the loop first
        self._stop_recording_event.set()

        if self.recording_thread and self.recording_thread.is_alive():
            print(f"Waiting for recording thread {self.camera_id} to finish...")
            self.recording_thread.join(timeout=2.0)
            if self.recording_thread.is_alive():
                print(f"Warning: Recording thread {self.camera_id} did not exit cleanly after 2 seconds.")
        self.recording_thread = None
        print(f"Recording thread for {self.camera_id} stopped/joined.")

        if not called_from_release_all:
            self._release_capture_if_unused() # Attempt to release main capture
        
        print(f"Recording stopped for camera {self.camera_id}.")

    def record_video(self):
        current_capture_for_thread = self._ensure_capture_initialized() # Get the shared capture
        if not current_capture_for_thread or not current_capture_for_thread.isOpened():
            print(f"Record_video: Main capture 'self.capture' not available or not open for cam {self.camera_id}. Exiting thread.")
            self.recording = False # Ensure state is correct
            return
        
        print(f"Record_video loop started for camera {self.camera_id} using shared capture.")
        while self.recording and not self._stop_recording_event.is_set():
            with self.capture_lock: # Protect reading from the shared capture
                if not self.capture or not self.capture.isOpened():
                    print(f"Error: Camera {self.camera_id} SHARED capture became unopened. Stopping recording.")
                    self.recording = False
                    break
                
                # Create VideoWriter inside the lock to ensure capture is good
                timestamp = time.strftime("%Y%m%d-%H%M%S")
                filename = f"cam{self.camera_id}_{timestamp}.mp4"
                filepath = os.path.join(self.recordings_dir, filename)
                fourcc = cv2.VideoWriter_fourcc(*"avc1")
                out = None
                
                try:
                    frame_width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
                    frame_height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    if frame_width == 0 or frame_height == 0:
                        print(f"Error: Invalid frame dimensions ({frame_width}x{frame_height}) for camera {self.camera_id}. Skipping segment.")
                        time.sleep(1)
                        continue 
                    fps = 15
                    out = cv2.VideoWriter(filepath, fourcc, fps, (frame_width, frame_height))
                    if not out.isOpened():
                        print(f"Error: Could not open VideoWriter for {filepath} on camera {self.camera_id}. Skipping segment.")
                        time.sleep(1)
                        continue
                except Exception as e:
                    print(f"Error creating VideoWriter for {filepath} on camera {self.camera_id}: {e}")
                    time.sleep(1)
                    continue

            # VideoWriter created successfully, now read frames and write
            # The lock is released here, frame reading happens outside the writer creation lock
            if out and out.isOpened():
                frame_count = 0
                total_frames = int(fps * self.settings.get("video_duration", 5))
                
                while self.recording and not self._stop_recording_event.is_set() and frame_count < total_frames:
                    frame_read_successfully = False
                    frame = None
                    with self.capture_lock: # Lock for reading the frame
                        if not self.capture or not self.capture.isOpened():
                            print(f"Error: Camera {self.camera_id} SHARED capture lost during segment write.")
                            self.recording = False; break
                        ret, frame_data = self.capture.read()
                        if ret:
                            frame = frame_data
                            frame_read_successfully = True
                    
                    if self._stop_recording_event.is_set(): break

                    if frame_read_successfully and frame is not None:
                        out.write(frame)
                        frame_count += 1
                    elif self.recording :
                        print(f"Warning: Could not read frame from camera {self.camera_id} (shared capture) during RECORDING segment.")
                        time.sleep(0.1) 
                
                out.release() # Release writer outside lock

            if frame_count > 0:
                    print(f"Finished segment: {filename} for camera {self.camera_id} ({frame_count} frames)")
            else:
                print(f"Segment {filename} for camera {self.camera_id} had 0 frames. Possible issue.")
                # Optionally delete empty file
                if os.path.exists(filepath) and os.path.getsize(filepath) == 0:
                    try:
                        os.remove(filepath)
                        print(f"Removed 0-byte file: {filepath}")
                    except Exception as e_del:
                        print(f"Error removing 0-byte file {filepath}: {e_del}")

            try:
                max_videos = self.settings.get("max_videos", 5)
                videos_with_ts = []
                if os.path.isdir(self.recordings_dir): # Check if directory exists first
                    for f in os.listdir(self.recordings_dir):
                        if f.endswith(".mp4"):
                            filepath = os.path.join(self.recordings_dir, f)
                            try:
                                ts = os.path.getctime(filepath)
                                videos_with_ts.append({'path': filepath, 'timestamp': ts, 'filename': f})
                            except FileNotFoundError:
                                print(f"[Pruning Warning Camera {self.camera_id}] File not found while getting timestamp: {filepath}")
                            except Exception as e:
                                 print(f"[Pruning Warning Camera {self.camera_id}] Error getting timestamp for {filepath}: {e}")
                else:
                    print(f"[Pruning Warning Camera {self.camera_id}] Recordings directory not found: {self.recordings_dir}")

                num_videos = len(videos_with_ts)
                print(f"[Pruning Check Camera {self.camera_id}] Found {num_videos} videos. Max allowed: {max_videos}")

                if num_videos > max_videos:
                    num_to_delete = num_videos - max_videos
                    print(f"[Pruning Camera {self.camera_id}] Need to delete {num_to_delete} oldest video(s).")
                    videos_with_ts.sort(key=lambda x: x['timestamp'])

                    for i in range(num_to_delete):
                        if i < len(videos_with_ts): 
                            video_to_remove = videos_with_ts[i] 
                            try:
                                os.remove(video_to_remove['path'])
                                print(f"[Pruning Camera {self.camera_id}] Removed oldest video: {video_to_remove['filename']}")
                            except OSError as e:
                                print(f"[Pruning Error Camera {self.camera_id}] Failed to remove {video_to_remove['path']}: {e}")
                        else:
                            print(f"[Pruning Warning Camera {self.camera_id}] Index {i} out of bounds during deletion loop.")
                            break 

            except Exception as e:
                print(f"[Pruning Error Camera {self.camera_id}] An unexpected error occurred during pruning setup/execution: {e}")

            if self.recording and not self._stop_recording_event.is_set():
                 time.sleep(0.01) # Short sleep between segments
        print(f"Record_video loop EXITED for camera {self.camera_id}. Final recording state: {self.recording}")

    def simulate_incident(self):
        incident_timestamp = time.strftime("%Y%m%d-%H%M%S")
        incident_folder_name = f"cam{self.camera_id}_incident_{incident_timestamp}"
        incident_folder_path = os.path.join(self.incidents_dir, incident_folder_name)        
        os.makedirs(incident_folder_path, exist_ok=True)
        for video in reversed(self.load_videos_from_folder(self.recordings_dir)):
            try:
                source_path = os.path.join(self.recordings_dir, video["filename"])
                destination_path = os.path.join(incident_folder_path, video["filename"])
                shutil.copy(source_path, destination_path)
                if len(os.listdir(incident_folder_path)) >= 6: # Check number of files copied
                    break
            except FileNotFoundError:
                print(f"Warning: Video file {video['filename']} not found during incident copy for camera {self.camera_id}.")
            except Exception as e:
                print(f"Error copying video {video['filename']} for incident: {e}")

    #Video Feed
    def generate_video_feed(self):
        print(f"Attempting to generate live feed for camera {self.camera_id}")

        if self.detection_enabled and not self.yolo_model_loaded_successfully:
            self._init_yolo_model()

        local_capture_ref = self._ensure_capture_initialized()

        if not local_capture_ref or not local_capture_ref.isOpened():
            print(f"Error: Could not get/initialize shared capture for LIVE FEED on camera {self.camera_id}")
            return

        with self.capture_lock:
            self.active_feed_clients += 1
        print(f"Successfully using shared capture for LIVE FEED on camera {self.camera_id}. Active clients: {self.active_feed_clients}")

        # Reset frame counter for this new feed session if desired,
        self.frame_counter_for_detection = 0

        try:
            while True:
                frame = None
                frame_available = False
                with self.capture_lock:
                    if not self.capture or not self.capture.isOpened():
                        print(f"Error: Camera {self.camera_id} SHARED capture lost during LIVE FEED.")
                        break
                    ret, frame_data = self.capture.read()
                    if ret:
                        frame = frame_data.copy()
                        frame_available = True

                if not frame_available:
                    print(f"Warning: Could not read frame from camera {self.camera_id} (shared capture) for LIVE FEED.")
                    time.sleep(0.1)
                    continue

                # Increment frame counter for detection logic
                self.frame_counter_for_detection += 1

                # --- Perform YOLO Detection (conditionally) ---
                if self.detection_enabled and self.yolo_model_loaded_successfully and self.yolo_model:
                    perform_new_detection = (self.frame_counter_for_detection % self.detection_skip_interval == 0)

                    if perform_new_detection:
                        self.last_known_detections = [] 
                        results = self.yolo_model(frame, stream=True, verbose=False)

                        for r in results:
                            boxes = r.boxes
                            for box in boxes:
                                x1_coord, y1_coord, x2_coord, y2_coord = map(int, box.xyxy[0])
                                conf_score = float(box.conf[0])
                                cls_idx = int(box.cls[0])
                                class_name_str = self.yolo_class_names_dict.get(cls_idx, "Unknown")

                                if conf_score >= self.yolo_confidence_threshold:
                                    color = self.yolo_class_colors.get(class_name_str.lower(), self.yolo_class_colors['UNKNOWN_CLASS'])
                                    label_text = f"{class_name_str}: {conf_score:.2f}"
                                    
                                    # Store information needed for drawing
                                    self.last_known_detections.append({
                                        "coords": (x1_coord, y1_coord, x2_coord, y2_coord),
                                        "label": label_text,
                                        "color": color
                                    })
               
                    for det in self.last_known_detections:
                        x1_coord, y1_coord, x2_coord, y2_coord = det["coords"]
                        label_to_draw = det["label"]
                        box_color = det["color"]

                        cv2.rectangle(frame, (x1_coord, y1_coord), (x2_coord, y2_coord), box_color, self.yolo_box_thickness)
                        (label_width, label_height), baseline = cv2.getTextSize(label_to_draw,
                                                                                cv2.FONT_HERSHEY_SIMPLEX,
                                                                                self.yolo_font_scale,
                                                                                self.yolo_text_thickness)
                        
                        label_y1 = max(y1_coord - label_height - 10, 0)
                        label_y2 = label_y1 + label_height + baseline + 10
                        label_x1 = x1_coord
                        label_x2 = x1_coord + label_width + 5

                        cv2.rectangle(frame, (label_x1, label_y1), (label_x2, label_y2), box_color, cv2.FILLED)
                        cv2.putText(frame, label_to_draw, (x1_coord + 5, label_y1 + label_height + 5),
                                    cv2.FONT_HERSHEY_SIMPLEX, self.yolo_font_scale, (0, 0, 0), self.yolo_text_thickness)
                # --- End YOLO Detection & Drawing block ---

                if self.recording:
                    cv2.rectangle(frame, (0, 0), (frame.shape[1] - 1, frame.shape[0] - 1), (0, 0, 255), 10)

                ret_encode, buffer = cv2.imencode(".jpg", frame)
                if not ret_encode or buffer is None:
                    print(f"Warning: cv2.imencode failed for LIVE FEED camera {self.camera_id}")
                    continue
                
                frame_bytes = buffer.tobytes()
                try:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")
                except GeneratorExit:
                    print(f"Client disconnected from live feed for camera {self.camera_id}.")
                    break
                except Exception as e_yield:
                    print(f"Error yielding frame for camera {self.camera_id} live feed: {e_yield}")
                    break
        except Exception as e_feed:
            print(f"Exception in generate_video_feed for camera {self.camera_id}: {e_feed}")
        finally:
            with self.capture_lock:
                self.active_feed_clients -= 1
            print(f"Live feed generation ended for camera {self.camera_id}. Active clients: {self.active_feed_clients}")
            self._release_capture_if_unused()


# Initialize Flask app
app = Flask(__name__, template_folder='static/templates')
app.secret_key = '14a6a86bf47bf75c4479c0c70886b2a5'


def save_cameras_to_json():
        camera_data = {camera_id: camera.to_dict() for camera_id, camera in cameras.items()}
        print("Saving cameras to JSON:", camera_data)  # Debugging line
        with open('cameras.json', 'w') as f:
            json.dump(camera_data, f)
        print("Cameras saved successfully.")
    
#Global variables for auto recording
auto_record_camera_id = "1"
auto_record_camera = None

def load_cameras_from_json():
    global cameras, auto_record_camera_id, auto_record_camera
    cameras = {} # Start fresh
    filepath = 'cameras.json'
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                loaded_data = json.load(f)
                for cam_id_key, camera_config_data in loaded_data.items():
                    # Ensure the config data itself contains the matching camera_id for from_dict
                    if 'camera_id' not in camera_config_data:
                         print(f"Warning: 'camera_id' key missing in config for {cam_id_key}. Using dictionary key as ID.")
                         camera_config_data['camera_id'] = cam_id_key # Add it for consistency

                    # Create Camera object using the config dictionary
                    try:
                        camera_obj = Camera.from_dict(camera_config_data)
                        cameras[cam_id_key] = camera_obj
                        if cam_id_key == auto_record_camera_id:
                            auto_record_camera = camera_obj
                            print(f"Designated auto-record camera '{auto_record_camera_id}' found in JSON.")
                    except Exception as e:
                         print(f"Error creating Camera object for ID {cam_id_key} from loaded data: {e}")

        except json.JSONDecodeError:
            print(f"Error decoding JSON from {filepath}. Starting with empty camera list.")
        except Exception as e:
            print(f"Error loading cameras from {filepath}: {e}. Starting with empty camera list.")

    if auto_record_camera is None and auto_record_camera_id not in cameras:
        print(f"Auto-record camera '{auto_record_camera_id}' not found. Creating a default one for device_index=0.")
        # Define default paths and settings for this camera
        default_rec_dir = f"src/recordings/camera{auto_record_camera_id}"
        default_inc_dir = f"src/incidents/camera{auto_record_camera_id}"
        default_set_file = f"src/settings_camera{auto_record_camera_id}.json"
        default_description = "Default Auto-Record Camera" # Or get from a config

        try:
            auto_record_camera = Camera(
                camera_id=auto_record_camera_id,
                recordings_dir=default_rec_dir,
                incidents_dir=default_inc_dir,
                settings_file=default_set_file,
                description=default_description,
                device_index=0 # Assuming device 0 is the target
            )
            cameras[auto_record_camera_id] = auto_record_camera # Add to the global cameras dictionary
            # Optionally save this new camera to cameras.json
            # save_cameras_to_json() # Be careful if you want to persist this only if it didn't exist
            print(f"Default auto-record camera '{auto_record_camera_id}' created.")
        except Exception as e:
            print(f"Error creating default auto-record camera: {e}")
            auto_record_camera = None # Ensure it's None if creation failed

load_cameras_from_json()

#Load users
def validate_user(username, password):
    current_dir = os.path.dirname(os.path.abspath(__file__))
    users_path = os.path.join(current_dir, 'users.json')
    try:
        if not os.path.exists(users_path):
            raise FileNotFoundError(f"users.json not found at {users_path}")
        with open(users_path) as f:
            data = json.load(f)
            print("DEBUG - Loaded data:", data)  # Debugging line
            
        users = data.get('users', [])
        return any(
            user.get('username') == username.strip() and 
            user.get('password') == password.strip()
            for user in users
        )
    except Exception as e:
        print(f"Auth Error: {str(e)}")
        return False


#Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/", methods=['GET'])
def home():
    if 'authenticated' in session:
        return redirect(url_for('camera_list'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        
        if validate_user(username, password):
            session['authenticated'] = True
            session['username'] = username
            return redirect(url_for('camera_list'))
        error = 'Invalid credentials. Please try again.'
    return render_template('login.html', error = error)

@app.route('/index/<camera_id>')
@login_required
def index(camera_id):
    camera = cameras.get(camera_id)  # Get the camera instance from the dictionary
    if camera is None:
        return "Camera not found", 404  # Handle the case where the camera ID is invalid

    return render_template('index.html', username=session['username'], camera_id=camera_id, description=camera.description)  # Pass camera_id to the template

@app.route('/camera_list')
@login_required
def camera_list():
    if not session.get('authenticated'):
        return redirect(url_for('login'))
    return render_template('camera_list.html', username=session['username'], cameras = cameras)

#Add new camera
@app.route('/add_camera', methods=['GET', 'POST'])
@login_required
def add_camera():
    error = None
    if request.method == 'POST':
        description = request.form.get('description', '').strip()
        
        next_id_int = 1
        if cameras: 
            numeric_ids = []
            for key in cameras.keys():
                try: 
                    numeric_ids.append(int(key))
                except ValueError:
                    print(f"Warning: Non-integer camera key found and ignored: {key}")
                    pass
            if numeric_ids:
                next_id_int = max(numeric_ids) + 1

        new_camera_id_str = str(next_id_int)  # Convert to string for the camera ID
        
        if error is None:
            try:
                # Use the validated string ID as the key
                camera_id_key = new_camera_id_str
                new_camera = Camera(
                    camera_id=camera_id_key, # Pass the ID here
                    recordings_dir=f"src/recordings/camera{camera_id_key}",
                    incidents_dir=f"src/incidents/camera{camera_id_key}",
                    settings_file=f"src/settings_camera{camera_id_key}.json",
                    description=description
                )
                cameras[camera_id_key] = new_camera
                save_cameras_to_json()  # Save the updated cameras to JSON
                return redirect(url_for('camera_list'))
            except Exception as e: 
                print(f"Error creating camera {camera_id_key}: {e}")
                error = "An unexpected error occurred while adding the camera."
                return render_template('add_camera.html', error=error)

    return render_template('add_camera.html', error =error)

#Delete camera
@app.route('/delete_camera/<camera_id>', methods=['POST'])
@login_required
def delete_camera(camera_id):
    if camera_id in cameras:
        camera_to_delete = cameras[camera_id]
        camera_to_delete.release_capture()

        recordings_path = camera_to_delete.recordings_dir
        incidents_path = camera_to_delete.incidents_dir
        settings_path = camera_to_delete.settings_file
        try: 
            del cameras[camera_id]
            save_cameras_to_json()
            if recordings_path and os.path.exists(recordings_path):
                shutil.rmtree(recordings_path)
                print(f"Successfully deleted directory: {recordings_path}")
                print(f"Directory not found or path invalid, skipping deletion: {recordings_path}")

            if incidents_path and os.path.exists(incidents_path):
                shutil.rmtree(incidents_path)
                print(f"Successfully deleted directory: {incidents_path}")
            else:
                print(f"Directory not found or path invalid, skipping deletion: {incidents_path}")

            if settings_path and os.path.exists(settings_path):
                os.remove(settings_path)
                print(f"Successfully deleted file: {settings_path}")
            else:
                print(f"Settings file not found or path invalid, skipping deletion: {settings_path}")

            return redirect(url_for('camera_list'))
        except OSError as e:
            print(f"Error deleting files/folders for camera {camera_id}: {e}")
            return redirect(url_for('camera_list'))
        
    return "Camera not found", 404  


@app.route('/feed_view')
@login_required
def feed_view():
    if not session.get('authenticated'):
        return redirect(url_for('login'))
    return render_template('feed_view.html', username=session['username'])

@app.route('/logout', methods=['POST'])
def logout():
    global cameras
    if cameras: # Check if the global dictionary exists and is populated
        for camera_id, camera in cameras.items():
            if isinstance(camera, Camera):
                try:
                    camera.release_capture()
                except Exception as e:
                    print(f"Error releasing camera {camera_id} during logout: {e}")
        print("Finished attempting camera releases for logout.")
    else:
        print("No 'cameras' dictionary found or it's empty during logout.")
    
    session.clear()
    return redirect(url_for('login'))


# Flask routes and functions
@app.route('/videos/<camera_id>')
@login_required
def videos(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        return "Camera not found", 404
    print(f"\n[DEBUG /videos/{camera_id}] Loading videos from: {camera.recordings_dir}")
    videos_list = camera.load_videos_from_folder(camera.recordings_dir)
    print(f"[DEBUG /videos/{camera_id}] BEFORE SORT ({len(videos_list)} items):")
    for v in videos_list:
        print(f"  - {v.get('filename')}: {v.get('raw_timestamp')}")

    sorted_videos = sorted(videos_list, key=lambda v: v.get('raw_timestamp', 0), reverse=True)

    print(f"[DEBUG /videos/{camera_id}] AFTER SORT ({len(sorted_videos)} items):")
    for v in sorted_videos:
        print(f"  - {v.get('filename')}: {v.get('raw_timestamp')}")
    print("-" * 20)

    return render_template("videos.html", videos=sorted_videos, camera_id=camera_id, username=session['username'], description=camera.description )
@app.route('/incident_videos/<camera_id>')
@login_required
def incident_videos(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        return "Camera not found", 404
    incident_videos_list = camera.load_incident_videos()
    sorted_incident_videos = sorted(incident_videos_list, key=lambda v: v.get('raw_timestamp', 0), reverse=True)
    return render_template("incident_vid.html", incident_videos=sorted_incident_videos, camera_id=camera_id, username=session['username'], description=camera.description)

@app.route('/settings_page/<camera_id>')
@login_required
def settings_page(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        return "Camera not found", 404
    current_settings = {}
    try:
        with open(camera.settings_file, 'r') as f:
            current_settings = json.load(f)
    except Exception as e:
        print(f"Error loading settings for camera {camera_id}: {e}")
        current_settings = {
            "max_videos": "Error loading",
            "video_duration": "Error loading"
        }
    return render_template('settings.html', camera_id=camera_id, username=session['username'], current_settings=current_settings, description=camera.description)
    #return render_template('settings.html', camera_id=camera_id, username=session['username'])

@app.route('/update_settings/<camera_id>', methods=['POST'])
@login_required
def update_settings(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        flash(f"Camera {camera_id} not found.", 'error')
        return redirect(url_for("camera_list"))

    max_videos_str = request.form.get("max_videos")
    video_duration_str = request.form.get("video_duration")

    error = None 
    validated_max_videos = None
    validated_video_duration = None

    if not max_videos_str:
        error = "Max Videos value cannot be empty."
    elif not video_duration_str:
        error = "Video Duration value cannot be empty."
    else:
        try:
            validated_max_videos = int(max_videos_str)
            validated_video_duration = int(video_duration_str)
            if validated_max_videos <= 0:
                error = "Max Videos must be a positive number (greater than zero)."
            elif validated_video_duration <= 0:
                error = "Video Duration must be a positive number (greater than zero)."

        except ValueError:
            error = "Max Videos and Video Duration must be valid whole numbers."

    if error:
        flash(error, 'error')
        current_settings = {
            "max_videos": camera.settings.get("max_videos", 5),
            "video_duration": camera.settings.get("video_duration", 5)
        }
        return render_template('settings.html', camera_id=camera_id, current_settings=current_settings), 400 # Optional: 400 Bad Request status
    try:
        success = camera.update_settings(validated_max_videos, validated_video_duration)

        if success is False: 
             flash(f"Failed to save settings for Camera {camera_id}. Check server logs.", 'error')
        else:
             flash(f"Settings for Camera {camera_id} updated successfully.", 'success')
        return redirect(url_for("index", camera_id=camera_id))

    except Exception as e:
        print(f"ERROR during update_settings call for Camera {camera_id}: {e}")
        flash("An unexpected error occurred while updating settings.", 'error')
        current_settings = camera.settings
        return render_template('settings.html', camera_id=camera_id, current_settings=current_settings), 500 # Internal Server Error


@app.route("/update_info/<camera_id>", methods=["POST"])
def update_info(camera_id):
    return redirect(url_for("videos", camera_id=camera_id))

@app.route("/update_incident_info/<camera_id>", methods=["POST"])
def update_incident_info(camera_id):
    return redirect(url_for("incident_videos", camera_id=camera_id))


@app.route('/start_recording/<camera_id>', methods=['POST'])
@login_required
def start_recording(camera_id):
    camera = cameras.get(camera_id)  
    if camera is None:
        return "Camera not found", 404  

    if camera.recording:
        print(f"Camera {camera_id} is already recording.")
        return "Already recording", 200
    
    camera.start_recording_thread() # New way
    return "", 204

@app.route('/stop_recording/<camera_id>', methods=['POST'])
@login_required
def stop_recording(camera_id):
    camera = cameras.get(camera_id) 
    if camera is None:
        return "Camera not found", 404  

    camera.stop_recording_logic() # New way
    return "", 204

@app.route('/simulate_incident/<camera_id>', methods=['POST'])
@login_required
def simulate_incident(camera_id):
    camera = cameras.get(camera_id)  
    if camera is None:
        return "Camera not found", 404  

    camera.simulate_incident()
    return '', 204

@app.route("/video_feed/<camera_id>")
def video_feed(camera_id):
    camera = cameras.get(camera_id) 
    if camera is None:
        return "Camera not found", 404  

    return Response(camera.generate_video_feed(), mimetype="multipart/x-mixed-replace; boundary=frame")

#New Route for video playbacks
@app.route('/serve_video/<camera_id>/recordings/<filename>')
@login_required # Keep it protected
def serve_recorded_video(camera_id, filename):
    camera = cameras.get(camera_id)
    if not camera:
        return "Camera not found", 404
    try: 
        directory = os.path.abspath(camera.recordings_dir)
        print(f"Serving recorded video: directory='{directory}', filename='{filename}'") # Debug log
        return send_from_directory(directory, filename, as_attachment=False) # as_attachment=False tries to play inline
    except FileNotFoundError:
        print(f"File not found: {os.path.join(directory, filename)}") # Debug log
        return "Video file not found", 404
    except Exception as e:
        print(f"Error serving recorded video {filename} for camera {camera_id}: {e}")
        return "Error serving video", 500

#New Route for incident playbacks
@app.route('/serve_video/<camera_id>/incidents/<incident_folder>/<filename>')
@login_required
def serve_incident_video(camera_id, incident_folder, filename):
    camera = cameras.get(camera_id)
    if not camera:
        return "Camera not found", 404
    try:
        directory = os.path.abspath(os.path.join(camera.incidents_dir, incident_folder))
        print(f"Serving incident video: directory='{directory}', filename='{filename}'") # Debug log
        # Securely serve the file
        return send_from_directory(directory, filename, as_attachment=False)
    except FileNotFoundError:
        print(f"File not found: {os.path.join(directory, filename)}") # Debug log
        return "Incident video file not found", 404
    except Exception as e:
        print(f"Error serving incident video {filename} from {incident_folder} for camera {camera_id}: {e}")
        return "Error serving video", 500

#New route for video delete
@app.route('/delete_video/<camera_id>/<filename>', methods=['DELETE'])
@login_required
def delete_recorded_video(camera_id, filename):
    camera = cameras.get(camera_id)
    if not camera:
        print (f"[Delete Error] Camera {camera_id} not found for deletion.")
        return jsonify({"success": False, "error": "Camera not found"}), 404
    
    try: 
        base_recording_dir = os.path.abspath(camera.recordings_dir)
        file_path = os.path.abspath(os.path.join(base_recording_dir, filename))
        if not file_path.startswith(base_recording_dir):
            print(f"[Delete Error] Path traversal attempt detected or invalid filename for camera {camera_id}: {filename}")
            return jsonify({"success": False, "error": "Invalid filename or path"}), 400
        print(f"[Delete Request] Attempting to delete: {file_path}")

        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"[Delete Success] Deleted: {file_path}")
            return jsonify({"success": True}), 200
        else:
            print(f"[Delete Error] File not found: {file_path}")
            return jsonify({"success": False, "error": "File not found"}), 404
    except OSError as e: 
        print(f"[Delete Error] OS error deleting {file_path}: {e}")
        return jsonify({"success": False, "error": f"Server error deleting file: {e.strerror}"}), 500
    except Exception as e:
        print(f"[Delete Error] Unexpected error deleting {filename} for camera {camera_id}: {e}")
        return jsonify({"success": False, "error": "An unexpected server error occurred"}), 500

#New route for deleting incident videos
@app.route('/delete_incident_video/<camera_id>/<incident_folder>/<filename>', methods=['DELETE'])
@login_required
def delete_incident_video(camera_id, incident_folder, filename):
    camera = cameras.get(camera_id)
    if not camera:
        print(f"[Delete Incident Error] Camera {camera_id} not found.")
        return jsonify({"success": False, "error": "Camera not found"}), 404

    try:
        base_incident_folder_dir = os.path.abspath(os.path.join(camera.incidents_dir, incident_folder))

        if not base_incident_folder_dir.startswith(os.path.abspath(camera.incidents_dir)) or not os.path.isdir(base_incident_folder_dir):
             print(f"[Delete Incident Error] Invalid or non-existent incident folder specified: {incident_folder} for camera {camera_id}")
             return jsonify({"success": False, "error": "Invalid incident folder"}), 400

        file_path = os.path.abspath(os.path.join(base_incident_folder_dir, filename))

        if not file_path.startswith(base_incident_folder_dir):
            print(f"[Delete Incident Error] Path traversal attempt or invalid filename within incident folder for camera {camera_id}: {filename}")
            return jsonify({"success": False, "error": "Invalid filename or path"}), 400 # Bad Request

        print(f"[Delete Incident Request] Attempting to delete: {file_path}")

        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"[Delete Incident Success] Successfully deleted: {file_path}")
        
            return jsonify({"success": True})
        else:
            print(f"[Delete Incident Error] File not found: {file_path}")
            return jsonify({"success": False, "error": "File not found"}), 404

    except OSError as e:
        print(f"[Delete Incident Error] OS error deleting {file_path}: {e}")
        return jsonify({"success": False, "error": f"Server error deleting file: {e.strerror}"}), 500
    except Exception as e:
        print(f"[Delete Incident Error] Unexpected error deleting {filename} (incident) for camera {camera_id}: {e}")
        return jsonify({"success": False, "error": "An unexpected server error occurred"}), 500


def release_all_cameras():
    print("Releasing all camera captures on exit...")
    global cameras
    if cameras:
        for camera_id, camera_obj in cameras.items(): # Use a different variable name
             if isinstance(camera_obj, Camera):
                 camera_obj.release_capture() # Calls the Camera's release method
        print("Camera release attempts finished.")
    else:
        print("No camera objects found to release.")

atexit.register(release_all_cameras)

if __name__ == "__main__":
    load_cameras_from_json()

    def delayed_auto_start():
        time.sleep(2) 
        target_auto_camera = cameras.get(auto_record_camera_id)
        if target_auto_camera:
            print(f"[Delayed Auto-Start] Attempting to start auto-recording for camera: {target_auto_camera.camera_id} ({target_auto_camera.description})")
            if not target_auto_camera.recording:
                target_auto_camera.start_recording_thread()
            else:
                print(f"[Delayed Auto-Start] Camera {target_auto_camera.camera_id} already marked as recording. Skipping auto-start.")
        else:
            print(f"[Delayed Auto-Start] Auto-record camera ID '{auto_record_camera_id}' not found. Auto-recording NOT started.")

    auto_start_initiator_thread = Thread(target=delayed_auto_start)
    auto_start_initiator_thread.daemon = True
    auto_start_initiator_thread.start()
    
    host = '0.0.0.0'
    app.run(debug=True, host='0.0.0.0', use_reloader=False) 
        