#Cameras only initialized when details page is chosen
#Very fast initial load time, but camera initialization is delayed until the details page is loaded.
#Log out or stopping program will relase all cameras
#Potential issues with all camera feed view?

from flask import Flask, render_template, Response, request, redirect, url_for, jsonify, session, send_from_directory, flash
import os
import cv2
import json
from threading import Thread, Event # Import Event
import time
import shutil
from functools import wraps
import atexit
from picamera2 import Picamera2, Preview
import numpy as np
import pymcprotocol # <-- ADDED IMPORT


recordings_dir = "src/recordings"
incidents_dir = "src/incidents"
settings_file = "src/settings.json"

# --- PLC Configuration ---
PLC_IP_ADDRESS = "192.168.3.28"  # Replace with your PLC's IP
PLC_PORT = 5055
PLC_READ_REGISTER = "D100"      # The register to read for triggers
PLC_POLL_INTERVAL = 2           # Seconds between PLC reads (adjust as needed)
PLC_START_RECORD_VALUE = 1
PLC_STOP_RECORD_VALUE = 2
PLC_INCIDENT_TRIGGER_VALUE = 3 # Added for completeness, implement logic if needed
# --- End PLC Configuration ---


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
            print(f"  [load_videos_from_folder Camera {self.camera_id}] ERROR loading directory: {e}") # Added error detail
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
            # else: # Removed potentially verbose else
            #      print(f"      [load_videos_from_folder] Item is NOT an .mp4 file. Skipping.")

        # print(f"  [load_videos_from_folder Camera {self.camera_id}] Finished processing. Returning list with {len(videos)} videos.") # Can be verbose
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
        return True # Indicate success


    def record_video(self):
        capture = self._get_or_init_capture()
        if not capture:
            print(f"Cannot start recording for camera {self.camera_id}: Capture device not available.")
            self.recording = False
            return

        print(f"Camera {self.camera_id}: record_video thread started. Recording status: {self.recording}")

        while self.recording: # Outer loop for continuous recording segments
            if not self.recording: # Double check before starting a new segment
                break

            timestamp = time.strftime("%Y%m%d-%H%M%S")
            filename = f"cam{self.camera_id}_{timestamp}.mp4"
            filepath = os.path.join(self.recordings_dir, filename)
            print(f"Camera {self.camera_id}: Attempting to record to {filepath}")

            out = None # Initialize out here
            try:
                frame = self.camera.capture_array()
                if frame is None:
                    print(f"Error: Failed to capture initial frame from camera {self.camera_id}.")
                    # self.recording = False # Decide if this error should stop all recording attempts
                    time.sleep(1) # Wait before retrying
                    continue # Try to capture frame again in the next iteration of outer while

                height, width = frame.shape[:2]
                fourcc = cv2.VideoWriter_fourcc(*'XVID')
                fps = 15 # Adjust as needed
                out = cv2.VideoWriter(filepath, fourcc, fps, (width, height))

                start_time = time.time()
                video_duration = self.settings.get("video_duration", 5)
                print(f"Camera {self.camera_id}: Recording segment started. Duration: {video_duration}s.")

                # Inner loop for the duration of one video segment
                while self.recording and (time.time() - start_time) < video_duration:
                    current_frame = self.camera.capture_array()
                    if current_frame is None:
                        print(f"Warning: Could not read frame from camera {self.camera_id} during segment.")
                        time.sleep(0.1 / fps if fps > 0 else 0.01) # Sleep appropriately
                        continue
                    
                    frame_bgr = cv2.cvtColor(current_frame, cv2.COLOR_RGB2BGR)
                    out.write(frame_bgr)
                    # time.sleep(1/fps) # Optional: control frame rate more explicitly if capture_array is too fast

                if out: # Ensure out was initialized
                    out.release()
                    print(f"Camera {self.camera_id}: Finished recording segment to {filepath}")

            except Exception as e:
                print(f"Error during recording segment for {filepath}: {e}")
                if out: # Ensure out is released on error too
                    out.release()
                # self.recording = False # Decide if any error should stop all PLC-triggered recording
                time.sleep(1) # Wait a bit before trying a new segment if still self.recording
                continue # Continue to next iteration of outer while loop

            if not self.recording: # Check again if we were told to stop during the segment
                break

            # Pruning logic (moved to be after each segment)
            try:
                max_videos = self.settings.get("max_videos", 5)
                videos_with_ts = []
                if os.path.isdir(self.recordings_dir):
                    for f_name in os.listdir(self.recordings_dir):
                        if f_name.endswith(".mp4"):
                            f_path = os.path.join(self.recordings_dir, f_name)
                            try:
                                ts = os.path.getctime(f_path)
                                videos_with_ts.append({'path': f_path, 'timestamp': ts, 'filename': f_name})
                            except Exception as e_ts:
                                print(f"[Pruning Warning Camera {self.camera_id}] Error getting timestamp for {f_path}: {e_ts}")
                else:
                    print(f"[Pruning Warning Camera {self.camera_id}] Recordings directory not found: {self.recordings_dir}")

                if len(videos_with_ts) > max_videos:
                    videos_with_ts.sort(key=lambda x: x['timestamp'])
                    num_to_delete = len(videos_with_ts) - max_videos
                    print(f"[Pruning Camera {self.camera_id}] Need to delete {num_to_delete} oldest video(s).")
                    for i in range(num_to_delete):
                        video_to_remove = videos_with_ts[i]
                        try:
                            os.remove(video_to_remove['path'])
                            print(f"[Pruning Camera {self.camera_id}] Removed oldest video: {video_to_remove['filename']}")
                        except OSError as e_rm:
                            print(f"[Pruning Error Camera {self.camera_id}] Failed to remove {video_to_remove['path']}: {e_rm}")
            except Exception as e_prune:
                print(f"[Pruning Error Camera {self.camera_id}] An unexpected error occurred during pruning: {e_prune}")
        
        print(f"Camera {self.camera_id}: record_video thread finished. Recording status: {self.recording}")
    

    def simulate_incident(self):
        incident_timestamp = time.strftime("%Y%m%d-%H%M%S")
        incident_folder_name = f"cam{self.camera_id}_incident_{incident_timestamp}"
        incident_folder_path = os.path.join(self.incidents_dir, incident_folder_name)        
        os.makedirs(incident_folder_path, exist_ok=True)
        
        # Ensure recordings_dir exists before trying to load videos from it
        if not os.path.isdir(self.recordings_dir):
            print(f"Warning: Recordings directory {self.recordings_dir} not found for incident simulation on camera {self.camera_id}.")
            return

        for video in reversed(self.load_videos_from_folder(self.recordings_dir)): # Make sure this folder path is correct
            try:
                source_path = os.path.join(self.recordings_dir, video["filename"]) # And this one
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
        capture = self._get_or_init_capture()
        if not capture:
            print(f"Cannot generate feed for camera {self.camera_id}: Capture device not available.")
            # Yield a placeholder or error image
            # For now, just return to avoid an unhandled generator
            return 

        while True:
            if self.camera is None: # Check if camera was released (e.g. during logout)
                print(f"Error: Camera {self.camera_id} capture lost during feed generation.")
                break 
            try:
                frame = self.camera.capture_array()
                if frame is None:
                    print(f"Warning: Could not read frame from camera {self.camera_id} for feed.")
                    time.sleep(0.1)
                    continue

                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

                if self.recording:
                    cv2.rectangle(
                        frame_bgr,
                        (0, 0),
                        (frame_bgr.shape[1] - 1, frame_bgr.shape[0] - 1),
                        (0, 0, 255), # Red border
                        10, # Thickness
                    )
                
                ret, buffer = cv2.imencode(".jpg", frame_bgr)
                if not ret:
                    print(f"Warning: JPEG encoding failed for camera {self.camera_id}")
                    time.sleep(0.1)
                    continue
                frame_bytes = buffer.tobytes()
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")
                time.sleep(0.03) # ~30 FPS, adjust as needed to reduce CPU if not necessary

            except Exception as e:
                print(f"Error in generate_video_feed for camera {self.camera_id}: {e}")
                # Consider how to handle this - break, try to reinit, or yield error frame
                break # Exit loop on error for now


# Initialize Flask app
app = Flask(__name__, template_folder='static/templates')
app.secret_key = '14a6a86bf47bf75c4479c0c70886b2a5' # CHANGE THIS FOR PRODUCTION
cameras = {} # Initialize cameras dictionary

# --- PLC Monitoring Globals ---
plc_thread_stop_event = Event()
plc_monitor_thread_obj = None # To store the thread object

def plc_monitor_thread_function(): # Renamed to avoid conflict if you name the thread itself 'plc_monitor_thread'
    global cameras
    plc = None
    last_plc_value = None 

    print("PLC Monitor: Thread started.")

    while not plc_thread_stop_event.is_set():
        try:
            if plc is None:
                print(f"PLC Monitor: Attempting to connect to {PLC_IP_ADDRESS}:{PLC_PORT}...")
                plc = pymcprotocol.Type3E()
                plc.set_timeout(2.5) # Slightly longer timeout
                plc.connect(PLC_IP_ADDRESS, PLC_PORT)
                print("PLC Monitor: Connected to PLC successfully.")
                last_plc_value = None # Reset last value on successful connect

            read_data = plc.batchread_wordunits(headdevice=PLC_READ_REGISTER, readsize=1)
            current_plc_value = read_data[0]

            if current_plc_value != last_plc_value:
                print(f"PLC Monitor: Read {PLC_READ_REGISTER} Value: {current_plc_value} (Previous: {last_plc_value})")
                
                for camera_id, camera_obj in list(cameras.items()): # Use list(cameras.items()) for safe iteration if cameras can be modified
                    if not isinstance(camera_obj, Camera):
                        continue

                    if current_plc_value == PLC_START_RECORD_VALUE:
                        if not camera_obj.recording:
                            print(f"PLC Monitor: Triggering START recording for camera {camera_id}")
                            if camera_obj._get_or_init_capture():
                                camera_obj.recording = True # Set recording true only if camera init succeeds
                                Thread(target=camera_obj.record_video, daemon=True).start()
                            else:
                                print(f"PLC Monitor: ERROR - Could not initialize camera {camera_id} for PLC-triggered recording.")
                        else:
                            print(f"PLC Monitor: Camera {camera_id} already recording, PLC start signal ({PLC_START_RECORD_VALUE}) ignored.")
                    
                    elif current_plc_value == PLC_STOP_RECORD_VALUE:
                        if camera_obj.recording:
                            print(f"PLC Monitor: Triggering STOP recording for camera {camera_id}")
                            camera_obj.recording = False
                        else:
                            print(f"PLC Monitor: Camera {camera_id} not recording, PLC stop signal ({PLC_STOP_RECORD_VALUE}) ignored.")
                    
                    elif current_plc_value == PLC_INCIDENT_TRIGGER_VALUE:
                        print(f"PLC Monitor: Triggering SIMULATE INCIDENT for camera {camera_id}")
                        # Ensure camera is initialized before simulating incident if it uses the camera stream
                        if camera_obj._get_or_init_capture():
                             Thread(target=camera_obj.simulate_incident, daemon=True).start() # Run in thread if it's long
                        else:
                            print(f"PLC Monitor: ERROR - Could not initialize camera {camera_id} for PLC-triggered incident.")
                
                last_plc_value = current_plc_value # Update last_plc_value only after processing all cameras for this change
            # else:
                # print(f"PLC Monitor: Value {PLC_READ_REGISTER} ({current_plc_value}) unchanged.") # Can be very verbose

        except pymcprotocol.McTimeoutError:
            print(f"PLC Monitor: Timeout communicating with PLC. Retrying...")
            if plc:
                try: plc.close()
                except: pass
            plc = None
            # last_plc_value remains to avoid re-triggering if connection restores with same value
            # Wait longer on timeout
            plc_thread_stop_event.wait(PLC_POLL_INTERVAL * 3) # Use wait for interruptible sleep
            continue # Skip the standard poll interval wait at the end

        except (pymcprotocol.McProtocolError, ConnectionRefusedError, OSError) as e: # Catch more specific network errors
            print(f"PLC Monitor: Communication Error ({type(e).__name__}): {e}")
            if plc:
                try: plc.close()
                except: pass
            plc = None
            last_plc_value = None # Reset on other errors to ensure re-evaluation
            plc_thread_stop_event.wait(PLC_POLL_INTERVAL * 2)
            continue
        except Exception as e:
            print(f"PLC Monitor: An unexpected error occurred: {e}")
            if plc:
                try: plc.close()
                except: pass
            plc = None
            last_plc_value = None
            plc_thread_stop_event.wait(PLC_POLL_INTERVAL * 2)
            continue
        
        plc_thread_stop_event.wait(PLC_POLL_INTERVAL)

    if plc:
        try:
            plc.close()
            print("PLC Monitor: Connection closed.")
        except Exception as e:
            print(f"PLC Monitor: Error closing PLC connection: {e}")
    print("PLC Monitor: Thread finished.")


def save_cameras_to_json():
        camera_data = {camera_id: camera.to_dict() for camera_id, camera in cameras.items()}
        # print("Saving cameras to JSON:", camera_data)
        try:
            with open('cameras.json', 'w') as f:
                json.dump(camera_data, f, indent=4) # Added indent for readability
            print("Cameras saved successfully.")
        except Exception as e:
            print(f"Error saving cameras to JSON: {e}")
    
def load_cameras_from_json():
    global cameras
    cameras = {} 
    filepath = 'cameras.json'
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                loaded_data = json.load(f)
                for cam_id_key, camera_config_data in loaded_data.items():
                    if 'camera_id' not in camera_config_data:
                         print(f"Warning: 'camera_id' key missing in config for {cam_id_key}. Using dictionary key as ID.")
                         camera_config_data['camera_id'] = cam_id_key

                    try:
                         cameras[cam_id_key] = Camera.from_dict(camera_config_data)
                    except Exception as e:
                         print(f"Error creating Camera object for ID {cam_id_key} from loaded data: {e}")
            print(f"Loaded {len(cameras)} cameras from JSON.")
        except json.JSONDecodeError:
            print(f"Error decoding JSON from {filepath}. Starting with empty camera list.")
        except Exception as e:
            print(f"Error loading cameras from {filepath}: {e}. Starting with empty camera list.")
    else:
        print(f"Cameras file {filepath} not found. Starting with empty camera list.")


load_cameras_from_json() # Load cameras at startup

#Load users
def validate_user(username, password):
    current_dir = os.path.dirname(os.path.abspath(__file__))
    users_path = os.path.join(current_dir, 'users.json') # Ensure users.json is in the same dir as this script
    try:
        if not os.path.exists(users_path):
            # Create a default users.json if it doesn't exist
            default_users = {"users": [{"username": "admin", "password": "password"}]} # CHANGE DEFAULT CREDENTIALS
            with open(users_path, 'w') as f_users:
                json.dump(default_users, f_users, indent=4)
            print(f"Created default users.json with admin/password. PLEASE CHANGE THE PASSWORD.")

        with open(users_path) as f:
            data = json.load(f)
            
        users_list = data.get('users', [])
        return any(
            user.get('username') == username.strip() and 
            user.get('password') == password.strip() # In a real app, hash passwords!
            for user in users_list
        )
    except Exception as e:
        print(f"Auth Error reading/validating users.json: {str(e)}")
        return False


#Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            flash("You need to be logged in to access this page.", "warning")
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/", methods=['GET'])
def home():
    if 'authenticated' in session:
        return redirect(url_for('camera_list'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('authenticated'):
        return redirect(url_for('camera_list'))
        
    error = None
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip() # DO NOT store/compare plain text passwords in production
        
        if validate_user(username, password):
            session['authenticated'] = True
            session['username'] = username
            flash('Login successful!', 'success')
            next_url = request.args.get('next')
            return redirect(next_url or url_for('camera_list'))
        error = 'Invalid credentials. Please try again.'
        flash(error, 'danger') # Use flash for errors too
    return render_template('login.html', error_message_from_server=error) # Pass error explicitly if needed by template

@app.route('/index/<camera_id>')
@login_required
def index(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        flash(f"Camera {camera_id} not found.", "danger")
        return redirect(url_for('camera_list'))

    return render_template('index.html', username=session['username'], camera_id=camera_id, description=camera.description, camera_obj=camera)

@app.route('/camera_list')
@login_required
def camera_list():
    return render_template('camera_list.html', username=session['username'], cameras = cameras)

#Add new camera
@app.route('/add_camera', methods=['GET', 'POST'])
@login_required
def add_camera():
    if request.method == 'POST':
        description = request.form.get('description', '').strip()
        if not description:
            flash("Camera description cannot be empty.", "danger")
            return render_template('add_camera.html')

        next_id_int = 1
        if cameras: 
            numeric_ids = [int(key) for key in cameras.keys() if key.isdigit()]
            if numeric_ids:
                next_id_int = max(numeric_ids) + 1
        
        new_camera_id_str = str(next_id_int)
        
        try:
            new_camera = Camera(
                camera_id=new_camera_id_str,
                recordings_dir=os.path.join(recordings_dir, f"camera{new_camera_id_str}"),
                incidents_dir=os.path.join(incidents_dir, f"camera{new_camera_id_str}"),
                settings_file=os.path.join("src", f"settings_camera{new_camera_id_str}.json"), # Ensure src exists
                description=description
            )
            cameras[new_camera_id_str] = new_camera
            save_cameras_to_json()
            flash(f"Camera '{description}' (ID: {new_camera_id_str}) added successfully.", "success")
            return redirect(url_for('camera_list'))
        except Exception as e: 
            print(f"Error creating camera {new_camera_id_str}: {e}")
            flash(f"An unexpected error occurred while adding the camera: {str(e)}", "danger")
            return render_template('add_camera.html', description=description) # Retain form data

    return render_template('add_camera.html')

#Delete camera
@app.route('/delete_camera/<camera_id>', methods=['POST'])
@login_required
def delete_camera(camera_id):
    if camera_id in cameras:
        camera_to_delete = cameras[camera_id]
        
        if camera_to_delete.recording: # Stop recording if active
            camera_to_delete.recording = False
            time.sleep(0.5) # Give recording thread a moment to stop

        camera_to_delete.release_capture()

        paths_to_delete = [
            camera_to_delete.recordings_dir,
            camera_to_delete.incidents_dir,
            camera_to_delete.settings_file
        ]
        
        try: 
            del cameras[camera_id]
            save_cameras_to_json()

            for path_item in paths_to_delete:
                if path_item and os.path.exists(path_item):
                    if os.path.isdir(path_item):
                        shutil.rmtree(path_item)
                        print(f"Successfully deleted directory: {path_item}")
                    elif os.path.isfile(path_item):
                        os.remove(path_item)
                        print(f"Successfully deleted file: {path_item}")
                else:
                    print(f"Path not found or invalid, skipping deletion: {path_item}")
            
            flash(f"Camera ID {camera_id} and its data deleted successfully.", "success")
            return redirect(url_for('camera_list'))
        except OSError as e:
            print(f"Error deleting files/folders for camera {camera_id}: {e}")
            flash(f"Error deleting files/folders for camera {camera_id}: {str(e)}", "danger")
            return redirect(url_for('camera_list'))
        
    flash(f"Camera {camera_id} not found for deletion.", "warning")
    return redirect(url_for('camera_list'))


@app.route('/feed_view') # Consider if this page needs to show feeds for ALL cameras or a selection
@login_required
def feed_view():
    # This template would need to be designed to dynamically load feeds for all cameras.
    # For simplicity, it might be better to have this link to individual camera /index pages.
    return render_template('feed_view.html', username=session['username'], cameras=cameras)

@app.route('/logout', methods=['POST', 'GET']) # Allow GET for easy logout link
@login_required # Ensure user is logged in to log out
def logout():
    # Camera release is handled by atexit, but good to stop active recordings explicitly
    for camera_id, camera_obj in cameras.items():
        if camera_obj.recording:
            camera_obj.recording = False
            print(f"Stopped recording for camera {camera_id} during logout.")
    
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


# Flask routes and functions
@app.route('/videos/<camera_id>')
@login_required
def videos(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        flash(f"Camera {camera_id} not found.", "danger")
        return redirect(url_for('camera_list'))
        
    videos_list = camera.load_videos_from_folder(camera.recordings_dir)
    sorted_videos = sorted(videos_list, key=lambda v: v.get('raw_timestamp', 0), reverse=True)
    return render_template("videos.html", videos=sorted_videos, camera_id=camera_id, username=session['username'], description=camera.description, camera_obj=camera)

@app.route('/incident_videos/<camera_id>')
@login_required
def incident_videos(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        flash(f"Camera {camera_id} not found.", "danger")
        return redirect(url_for('camera_list'))
    incident_videos_list = camera.load_incident_videos()
    sorted_incident_videos = sorted(incident_videos_list, key=lambda v: v.get('raw_timestamp', 0), reverse=True)
    return render_template("incident_vid.html", incident_videos=sorted_incident_videos, camera_id=camera_id, username=session['username'], description=camera.description, camera_obj=camera)

@app.route('/settings_page/<camera_id>')
@login_required
def settings_page(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        flash(f"Camera {camera_id} not found.", "danger")
        return redirect(url_for('camera_list'))
    
    # current_settings are already loaded into camera.settings by camera.load_settings()
    return render_template('settings.html', camera_id=camera_id, username=session['username'], current_settings=camera.settings, description=camera.description, camera_obj=camera)


@app.route('/update_settings/<camera_id>', methods=['POST'])
@login_required
def update_settings(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        flash(f"Camera {camera_id} not found.", 'danger')
        return redirect(url_for("camera_list"))

    try:
        max_videos = int(request.form.get("max_videos"))
        video_duration = int(request.form.get("video_duration"))

        if max_videos <= 0 or video_duration <= 0:
            flash("Max Videos and Video Duration must be positive numbers.", 'danger')
        else:
            if camera.update_settings(max_videos, video_duration):
                save_cameras_to_json() # Persist settings if Camera class doesn't do it internally
                flash(f"Settings for Camera {camera_id} updated successfully.", 'success')
                return redirect(url_for("index", camera_id=camera_id))
            else:
                flash(f"Failed to update settings for Camera {camera_id}.", 'danger')
    except ValueError:
        flash("Max Videos and Video Duration must be valid whole numbers.", 'danger')
    except Exception as e:
        print(f"ERROR during update_settings for Camera {camera_id}: {e}")
        flash(f"An unexpected error occurred: {str(e)}", 'danger')
    
    # On error, re-render settings page with current (or attempted) values
    return render_template('settings.html', camera_id=camera_id, current_settings=request.form, description=camera.description, username=session['username'], camera_obj=camera)


@app.route('/start_recording/<camera_id>', methods=['POST'])
@login_required
def start_recording(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        return jsonify({"success": False, "error": "Camera not found"}), 404
    
    if camera.recording:
        return jsonify({"success": False, "message": "Already recording"}), 200 # Or 409 Conflict

    if camera._get_or_init_capture():
        camera.recording = True
        Thread(target=camera.record_video, daemon=True).start()
        flash(f"Started recording for camera {camera_id}.", "info")
        return jsonify({"success": True, "message": "Recording started"}), 200
    else:
        flash(f"Could not initialize camera {camera_id} to start recording.", "danger")
        return jsonify({"success": False, "error": "Failed to initialize camera"}), 500


@app.route('/stop_recording/<camera_id>', methods=['POST'])
@login_required
def stop_recording(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        return jsonify({"success": False, "error": "Camera not found"}), 404

    if not camera.recording:
        return jsonify({"success": False, "message": "Not recording"}), 200

    camera.recording = False
    flash(f"Stopped recording for camera {camera_id}.", "info")
    return jsonify({"success": True, "message": "Recording stopped"}), 200

@app.route('/simulate_incident/<camera_id>', methods=['POST'])
@login_required
def simulate_incident_route(camera_id): # Renamed to avoid conflict
    camera = cameras.get(camera_id)
    if camera is None:
        return jsonify({"success": False, "error": "Camera not found"}), 404
    
    if camera._get_or_init_capture(): # Ensure camera is ready if simulate_incident needs it
        Thread(target=camera.simulate_incident, daemon=True).start()
        flash(f"Incident simulation triggered for camera {camera_id}.", "info")
        return jsonify({"success": True, "message": "Incident simulation started"}), 200
    else:
        flash(f"Could not initialize camera {camera_id} for incident simulation.", "danger")
        return jsonify({"success": False, "error": "Failed to initialize camera"}), 500


@app.route("/video_feed/<camera_id>")
@login_required # Usually video feeds are also protected
def video_feed(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        # Return a placeholder image or a 404 text
        # For now, let's return a simple text response for error
        return Response("Camera not found", status=404, mimetype='text/plain')
    return Response(camera.generate_video_feed(), mimetype="multipart/x-mixed-replace; boundary=frame")

# --- Video Serving and Deletion Routes (largely unchanged, minor path safety) ---
@app.route('/serve_video/<camera_id>/recordings/<path:filename>') # Added path converter for filename
@login_required
def serve_recorded_video(camera_id, filename):
    camera = cameras.get(camera_id)
    if not camera: return "Camera not found", 404
    try: 
        directory = os.path.abspath(camera.recordings_dir)
        # Basic path traversal check
        if not os.path.abspath(os.path.join(directory, filename)).startswith(directory):
            return "Forbidden", 403
        return send_from_directory(directory, filename, as_attachment=False)
    except FileNotFoundError: return "Video file not found", 404
    except Exception as e:
        print(f"Error serving recorded video {filename} for camera {camera_id}: {e}")
        return "Error serving video", 500

@app.route('/serve_video/<camera_id>/incidents/<incident_folder>/<path:filename>')
@login_required
def serve_incident_video(camera_id, incident_folder, filename):
    camera = cameras.get(camera_id)
    if not camera: return "Camera not found", 404
    try:
        base_incident_dir = os.path.abspath(camera.incidents_dir)
        directory = os.path.abspath(os.path.join(base_incident_dir, incident_folder))
        # Basic path traversal check
        if not directory.startswith(base_incident_dir) or \
           not os.path.abspath(os.path.join(directory, filename)).startswith(directory):
            return "Forbidden", 403
        return send_from_directory(directory, filename, as_attachment=False)
    except FileNotFoundError: return "Incident video file not found", 404
    except Exception as e:
        print(f"Error serving incident video {filename} from {incident_folder} for cam {camera_id}: {e}")
        return "Error serving video", 500

@app.route('/delete_video/<camera_id>/<path:filename>', methods=['DELETE'])
@login_required
def delete_recorded_video(camera_id, filename):
    camera = cameras.get(camera_id)
    if not camera: return jsonify({"success": False, "error": "Camera not found"}), 404
    
    try: 
        base_recording_dir = os.path.abspath(camera.recordings_dir)
        file_path = os.path.abspath(os.path.join(base_recording_dir, filename))
        if not file_path.startswith(base_recording_dir):
            return jsonify({"success": False, "error": "Invalid filename or path"}), 400
        
        if os.path.exists(file_path) and os.path.isfile(file_path): # Ensure it's a file
            os.remove(file_path)
            return jsonify({"success": True, "message": f"Deleted {filename}"}), 200
        else:
            return jsonify({"success": False, "error": "File not found"}), 404
    except Exception as e:
        print(f"[Delete Error] Unexpected error deleting {filename} for camera {camera_id}: {e}")
        return jsonify({"success": False, "error": "Server error deleting file"}), 500

@app.route('/delete_incident_video/<camera_id>/<incident_folder>/<path:filename>', methods=['DELETE'])
@login_required
def delete_incident_video(camera_id, incident_folder, filename):
    camera = cameras.get(camera_id)
    if not camera: return jsonify({"success": False, "error": "Camera not found"}), 404

    try:
        base_incident_dir = os.path.abspath(camera.incidents_dir)
        incident_folder_path = os.path.abspath(os.path.join(base_incident_dir, incident_folder))

        if not incident_folder_path.startswith(base_incident_dir) or not os.path.isdir(incident_folder_path):
             return jsonify({"success": False, "error": "Invalid incident folder"}), 400

        file_path = os.path.abspath(os.path.join(incident_folder_path, filename))
        if not file_path.startswith(incident_folder_path):
            return jsonify({"success": False, "error": "Invalid filename or path"}), 400

        if os.path.exists(file_path) and os.path.isfile(file_path):
            os.remove(file_path)
            return jsonify({"success": True, "message": f"Deleted {filename} from {incident_folder}"}), 200
        else:
            return jsonify({"success": False, "error": "File not found"}), 404
    except Exception as e:
        print(f"[Delete Incident Error] Unexpected error deleting {filename} from {incident_folder} for cam {camera_id}: {e}")
        return jsonify({"success": False, "error": "Server error deleting file"}), 500


def release_all_cameras():
    print("Releasing all camera captures on exit...")
    global cameras
    if cameras:
        for cam_id, cam_obj in cameras.items():
             if isinstance(cam_obj, Camera):
                 if cam_obj.recording: # Stop recording before releasing
                     cam_obj.recording = False
                 cam_obj.release_capture()
        print("Camera release attempts finished.")
    else:
        print("No camera objects found to release.")

def stop_plc_monitor(): # Renamed
    global plc_monitor_thread_obj # Use the correct global name
    if plc_monitor_thread_obj and plc_monitor_thread_obj.is_alive():
        print("Attempting to stop PLC monitor thread...")
        plc_thread_stop_event.set()
        plc_monitor_thread_obj.join(timeout=PLC_POLL_INTERVAL + 3) # Give it time to finish current poll + a bit more
        if plc_monitor_thread_obj.is_alive():
            print("PLC monitor thread did not stop in time.")
        else:
            print("PLC monitor thread stopped successfully.")

atexit.register(release_all_cameras)
atexit.register(stop_plc_monitor)


if __name__ == "__main__":
    # Create src directory if it doesn't exist, for settings files
    if not os.path.exists("src"):
        os.makedirs("src")
        print("Created 'src' directory for settings files.")

    load_cameras_from_json() # Moved after src creation potentially
    host = '0.0.0.0'
    
    # --- Start PLC Monitoring Thread ---
    # Handle Flask's reloader correctly to avoid starting the thread twice
    # This check ensures the thread starts only in the main process or when debug is off
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        if not cameras:
            print("No cameras configured. PLC monitoring will not control any cameras but will still run.")
        
        print("Starting PLC monitoring thread...")
        plc_thread_stop_event.clear()
        plc_monitor_thread_obj = Thread(target=plc_monitor_thread_function, daemon=True)
        plc_monitor_thread_obj.start()
    else:
        print("Flask is in debug mode with reloader active. PLC thread will start in the reloaded process.")
    # --- End Start PLC Monitoring Thread ---

    app.run(debug=True, host='0.0.0.0', port=5001, use_reloader=True) # use_reloader=True is default with debug=True