#Cameras only initialized when details page is chosen
#Very fast initial load time, but camera initialization is delayed until the details page is loaded.
#Log out or stopping program will relase all cameras
#Potential issues with all camera feed view?

from flask import Flask, render_template, Response, request, redirect, url_for, jsonify, session, send_from_directory, flash
import os
import cv2
import json
from threading import Thread
import time
import shutil
from functools import wraps
import atexit
from picamera2 import Picamera2, Preview
import numpy as np
import pymcprotocol

#server2.py code with picamera2 instead of cv2
#No database
#run on raspberry pi (running raspberry pi OS)  04062025

recordings_dir = "src/recordings"
incidents_dir = "src/incidents"
settings_file = "src/settings.json"

PLC_IP_ADDRESS = "192.168.3.28"  # Replace with your PLC's IP
PLC_PORT = 5055
PLC_READ_REGISTER = "D100"      # The register to read for triggers
PLC_POLL_INTERVAL = 2           # Seconds between PLC reads (adjust as needed)
PLC_START_RECORD_VALUE = 1
PLC_STOP_RECORD_VALUE = 2
PLC_INCIDENT_TRIGGER_VALUE = 3


class Camera:
    #device index=0 -> webcam
    def __init__(self, camera_id, recordings_dir, incidents_dir, settings_file, description="", device_index=0):
        self.camera_id = str(camera_id)
        self.recordings_dir = recordings_dir
        self.incidents_dir = incidents_dir
        self.settings_file = settings_file
        self.description = description  # Add description attribute
        self.device_index = device_index
        self.camera = None
        self.recording = False
        
        os.makedirs(recordings_dir, exist_ok=True)
        os.makedirs(incidents_dir, exist_ok=True)
        self.load_settings()

    '''
    def _get_or_init_capture(self):
        """Initializes and returns the cv2.VideoCapture object if not already done."""
        if self.camera is None:
            try:
                print(f"Initializing cv2.VideoCapture for camera {self.camera_id} (device: {self.device_index})...")
                self.camera = cv2.VideoCapture(self.device_index)
                
                #self.camera = picamera2(self.device_index)
                
                if not self.camera.isOpened():
                    print(f"Error: Could not open video device {self.device_index} for camera {self.camera_id}")
                    self.camera = None # Reset if opening failed
                else:
                     print(f"Successfully opened camera {self.camera_id}")
            except Exception as e:
                print(f"Exception initializing cv2.VideoCapture for camera {self.camera_id}: {e}")
                self.camera = None
        return self.camera
        '''
    def _get_or_init_capture(self):
        """Initializes and returns the Picamera2 object if not already done."""
        if self.camera is None:
            try:
                print(f"Initializing Picamera2 for camera {self.camera_id}...")
                self.camera = Picamera2()
                self.camera.configure(self.camera.create_video_configuration())  # Configure for video
                self.camera.start()  # Start the camera
                print(f"Successfully opened camera {self.camera_id}")
            except Exception as e:
                print(f"Exception initializing Picamera2 for camera {self.camera_id}: {e}")
                self.camera = None
        return self.camera
        
        
    def release_capture(self):
        """Releases the Picamera2 object if it's initialized."""
        if self.camera is not None:
            print(f"Releasing capture for camera {self.camera_id}")
            self.camera.close()
            self.camera = None

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

    def record_video(self):
        capture = self._get_or_init_capture()  # Get/init camera first
        if not capture:
            print(f"Cannot start recording for camera {self.camera_id}: Capture device not available.")
            self.recording = False  # Ensure recording stops if init failed
            return  # Exit if camera couldn't be opened

        while self.recording:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            filename = f"cam{self.camera_id}_{timestamp}.mp4"
            filepath = os.path.join(self.recordings_dir, filename)
            print(f"Attempting to record to {filepath}")  # Debugging line
            try:
                # Read one frame to get frame size for VideoWriter
                frame = self.camera.capture_array()  # Capture a frame
                if frame is None:
                    print(f"Error: Failed to capture frame from camera {self.camera_id}.")
                    self.recording = False
                    break

                height, width = frame.shape[:2]

                # Define the codec and create VideoWriter object
                fourcc = cv2.VideoWriter_fourcc(*'XVID')  # Codec for mp4
                fps = 15  # Adjust as needed
                out = cv2.VideoWriter(filepath, fourcc, fps, (width, height))

                start_time = time.time()
                print(f"Recording started. Capturing frames at {fps} FPS for {self.settings.get('video_duration', 5)} seconds.")

                while self.recording and (time.time() - start_time) < self.settings.get("video_duration", 5):
                    frame = self.camera.capture_array()  # Capture frame (RGB)
                    if frame is None:
                        print(f"Warning: Could not read frame from camera {self.camera_id} during recording.")
                        time.sleep(0.1)
                        continue

                    # Convert RGB frame to BGR for OpenCV processing
                    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    out.write(frame_bgr)  # Write the frame to the video file

                out.release()  # Release the VideoWriter
                print(f"Camera {self.camera_id}: Finished recording to {filepath}")

            except Exception as e:
                print(f"Error during recording for {filepath}: {e}")
                self.recording = False  # Stop recording on error
        
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
        capture = self._get_or_init_capture()  # Get/init camera first
        if not capture:
            print(f"Cannot generate feed for camera {self.camera_id}: Capture device not available.")
            return
        while True:
            if self.camera is None:
                print(f"Error: Camera {self.camera_id} capture lost during feed generation.")
                break  # Exit the loop if camera fails

            frame = self.camera.capture_array()  # Capture frame (RGB)
            if frame is None:
                print(f"Warning: Could not read frame from camera {self.camera_id} for feed.")
                time.sleep(0.1)  # Avoid busy-looping
                continue  # Try reading next frame

            # Convert RGB frame to BGR for OpenCV processing
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            if self.recording:
                frame_bgr = cv2.rectangle(
                    frame_bgr,
                    (0, 0),
                    (frame_bgr.shape[1] - 1, frame_bgr.shape[0] - 1),
                    (0, 0, 255),
                    10,
                )
            # Encode frame for streaming
            _, buffer = cv2.imencode(".jpg", frame_bgr)
            frame_bytes = buffer.tobytes()
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")



# Initialize Flask app
app = Flask(__name__, template_folder='static/templates')
app.secret_key = '14a6a86bf47bf75c4479c0c70886b2a5'


plc_thread_stop_event = Thread.Event() # For cleanly stopping the PLC thread

def plc_monitor_thread():
    #Monitors a PLC register and triggers camera recording based on its value.
    global cameras
    plc = None
    last_plc_value = None # To detect changes

    print("PLC Monitor: Thread started.")

    while not plc_thread_stop_event.is_set():
        try:
            if plc is None:
                print(f"PLC Monitor: Attempting to connect to {PLC_IP_ADDRESS}:{PLC_PORT}...")
                plc = pymcprotocol.Type3E()
                # Set a timeout for connection and communication
                plc.set_timeout(2) # 2 seconds timeout
                plc.connect(PLC_IP_ADDRESS, PLC_PORT)
                print("PLC Monitor: Connected to PLC successfully.")

            # Read the PLC register
            # batchread_wordunits returns a list, e.g., [value]
            read_data = plc.batchread_wordunits(headdevice=PLC_READ_REGISTER, readsize=1)
            current_plc_value = read_data[0] # Get the first (and only) element

            if current_plc_value != last_plc_value: # Process only if value changed
                print(f"PLC Monitor: Read {PLC_READ_REGISTER} Value: {current_plc_value} (Previous: {last_plc_value})")
                last_plc_value = current_plc_value

                # --- PLC Trigger Logic ---
                # Iterate over all managed cameras
                for camera_id, camera_obj in cameras.items():
                    if not isinstance(camera_obj, Camera): 
                        continue

                    if current_plc_value == PLC_START_RECORD_VALUE:
                        if not camera_obj.recording:
                            print(f"PLC Monitor: Triggering START recording for camera {camera_id}")
                            camera_obj.recording = True
                            # Ensure camera is initialized before starting recording thread
                            if camera_obj._get_or_init_capture(): # Initialize if not already
                                Thread(target=camera_obj.record_video).start()
                            else:
                                print(f"PLC Monitor: ERROR - Could not initialize camera {camera_id} for PLC-triggered recording.")
                                camera_obj.recording = False # Reset state if init failed
                        else:
                            print(f"PLC Monitor: Camera {camera_id} already recording, PLC start signal ({PLC_START_RECORD_VALUE}) ignored.")

                    elif current_plc_value == PLC_STOP_RECORD_VALUE:
                        if camera_obj.recording:
                            print(f"PLC Monitor: Triggering STOP recording for camera {camera_id}")
                            camera_obj.recording = False
                            # The recording thread in camera_obj.record_video will see self.recording as False and stop.
                        else:
                            print(f"PLC Monitor: Camera {camera_id} not recording, PLC stop signal ({PLC_STOP_RECORD_VALUE}) ignored.")
            else:
                print(f"PLC Monitor: Value {PLC_READ_REGISTER} ({current_plc_value}) unchanged.")


        except pymcprotocol.McProtocolError as e:
            print(f"PLC Monitor: MC Protocol Error: {e}")
            if plc:
                plc.close()
            plc = None # Force reconnection attempt
            last_plc_value = None # Reset last value on error
            time.sleep(PLC_POLL_INTERVAL * 2) # Longer delay on protocol error
        except ConnectionRefusedError:
            print(f"PLC Monitor: Connection refused. Check PLC IP/Port and if PLC is listening.")
            if plc:
                plc.close()
            plc = None
            last_plc_value = None
            time.sleep(PLC_POLL_INTERVAL * 2) # Longer delay on connection error
        except Exception as e:
            print(f"PLC Monitor: An unexpected error occurred: {e}")
            if plc: # Attempt to close if plc object exists
                try:
                    plc.close()
                except: # Ignore errors during close on top of another error
                    pass
            plc = None # Reset plc to force reconnection
            last_plc_value = None
            time.sleep(PLC_POLL_INTERVAL * 2) # Wait a bit longer before retrying on generic errors

        plc_thread_stop_event.wait(PLC_POLL_INTERVAL)

    # Clean up PLC connection when thread is stopping
    if plc:
        try:
            plc.close()
            print("PLC Monitor: Connection closed.")
        except Exception as e:
            print(f"PLC Monitor: Error closing PLC connection: {e}")
    print("PLC Monitor: Thread finished.")

def save_cameras_to_json():
        camera_data = {camera_id: camera.to_dict() for camera_id, camera in cameras.items()}
        print("Saving cameras to JSON:", camera_data)  # Debugging line
        with open('cameras.json', 'w') as f:
            json.dump(camera_data, f)
        print("Cameras saved successfully.")
    
def load_cameras_from_json():
    global cameras
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
                         cameras[cam_id_key] = Camera.from_dict(camera_config_data)
                    except Exception as e:
                         print(f"Error creating Camera object for ID {cam_id_key} from loaded data: {e}")

        except json.JSONDecodeError:
            print(f"Error decoding JSON from {filepath}. Starting with empty camera list.")
        except Exception as e:
            print(f"Error loading cameras from {filepath}: {e}. Starting with empty camera list.")


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

    camera.recording = True
    Thread(target=camera.record_video).start()  
    return "", 204

@app.route('/stop_recording/<camera_id>', methods=['POST'])
@login_required
def stop_recording(camera_id):
    camera = cameras.get(camera_id) 
    if camera is None:
        return "Camera not found", 404  

    camera.recording = False
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
    if cameras: # Check if cameras dictionary exists
        for camera_id, camera in cameras.items():
             if isinstance(camera, Camera): # Ensure it's a Camera object
                 camera.release_capture()
        print("Camera release attempts finished.")
    else:
        print("No camera objects found to release.")

plc_monitor = None # Global variable for the thread object
def stop_plc_monitor_thread():
    global plc_monitor
    if plc_monitor and plc_monitor.is_alive():
        print("Attempting to stop PLC monitor thread...")
        plc_thread_stop_event.set() # Signal the thread to stop
        plc_monitor.join(timeout=PLC_POLL_INTERVAL + 5) # Wait for thread to finish
        if plc_monitor.is_alive():
            print("PLC monitor thread did not stop in time.")
        else:
            print("PLC monitor thread stopped successfully.")

atexit.register(release_all_cameras)
atexit.register(stop_plc_monitor_thread) # Register to stop on exit


if __name__ == "__main__":
    load_cameras_from_json()
    host = '0.0.0.0'
    if not cameras:
        print("No cameras configured. PLC monitoring will not control any cameras.")
    print("Starting PLC monitoring thread...")
    plc_thread_stop_event.clear() # Ensure event is not set initially
    plc_monitor = Thread(target=plc_monitor_thread, daemon=True) # daemon=True allows main program to exit even if thread is running
    plc_monitor.start()
    app.run(debug=True, host='0.0.0.0', port=5001)
        
