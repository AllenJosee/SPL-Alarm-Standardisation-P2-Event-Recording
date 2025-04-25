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
        self.description = description  # Add description attribute
        self.device_index = device_index
        self.camera = None
        self.recording = False
        
        os.makedirs(recordings_dir, exist_ok=True)
        os.makedirs(incidents_dir, exist_ok=True)
        self.load_settings()

    def _get_or_init_capture(self):
        """Initializes and returns the cv2.VideoCapture object if not already done."""
        if self.camera is None:
            try:
                print(f"Initializing cv2.VideoCapture for camera {self.camera_id} (device: {self.device_index})...")
                self.camera = cv2.VideoCapture(self.device_index)
                if not self.camera.isOpened():
                    print(f"Error: Could not open video device {self.device_index} for camera {self.camera_id}")
                    self.camera = None # Reset if opening failed
                else:
                     print(f"Successfully opened camera {self.camera_id}")
            except Exception as e:
                print(f"Exception initializing cv2.VideoCapture for camera {self.camera_id}: {e}")
                self.camera = None
        return self.camera
    
    def release_capture(self):
        """Releases the cv2.VideoCapture object if it's initialized."""
        if self.camera is not None and self.camera.isOpened():
            print(f"Releasing capture for camera {self.camera_id}")
            self.camera.release()
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
        # Expect camera_id to be in the data dictionary now
        if 'camera_id' not in data:
            # Handle cases where old JSON might not have it (optional)
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
        for file in os.listdir(folder):
            if file.endswith(".mp4"):
                filepath = os.path.join(folder, file)
                videos.append({"filename": file, "timestamp": time.ctime(os.path.getctime(filepath))})
        return videos

    def load_incident_videos(self):
        incident_videos = []
        for folder in os.listdir(self.incidents_dir):
            folder_path = os.path.join(self.incidents_dir, folder)
            if os.path.isdir(folder_path):
                for file in os.listdir(folder_path):
                    if file.endswith(".mp4"):
                        file_path = os.path.join(folder_path, file)
                        incident_videos.append({"filename": file, "path": file_path})
        return incident_videos

    
    def update_settings(self, max_videos, video_duration):
        self.settings["max_videos"] = max_videos
        self.settings["video_duration"] = video_duration
        with open(self.settings_file, "w") as f:
            json.dump(self.settings, f)

    def record_video(self):
        capture = self._get_or_init_capture() # Get/init camera first
        if not capture:
            print(f"Cannot start recording for camera {self.camera_id}: Capture device not available.")
            self.recording = False # Ensure recording stops if init failed
            return # Exit if camera couldn't be opened
        
        while self.recording:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            filename = f"cam{self.camera_id}_{timestamp}.mp4"
            
            filepath = os.path.join(self.recordings_dir, filename)
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            
            try:
                # Ensure capture is still valid before creating writer
                if not capture.isOpened():
                     print(f"Error: Camera {self.camera_id} capture lost before creating VideoWriter.")
                     self.recording = False # Stop trying to record
                     break

                frame_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
                frame_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = capture.get(cv2.CAP_PROP_FPS)
                if fps <= 0 : fps = 15 # Default if FPS query fails

                print(f"Camera {self.camera_id}: Creating writer for {filepath} ({frame_width}x{frame_height} @ {fps} FPS)")

                out = cv2.VideoWriter(filepath, fourcc, fps, (frame_width, frame_height))
                if not out.isOpened():
                    print(f"Error: Could not open VideoWriter for {filepath}")
                    time.sleep(1)
                    continue
            except Exception as e:
                print(f"Error creating VideoWriter for {filepath}: {e}")
                time.sleep(1)
                continue

            start_time = time.time()
            frame_count = 0
            total_frames = int(15 * self.settings["video_duration"])
            while self.recording and frame_count < total_frames:
                if not capture.isOpened():
                    print(f"Error: Camera {self.camera_id} capture lost during recording.")
                    self.recording = False # Stop loop
                    break

                ret, frame = capture.read()
                if ret:
                    out.write(frame)
                    frame_count += 1
                else:
                    print(f"Warning: Could not read frame from camera {self.camera_id}")
                    time.sleep(0.1) 
            out.release()

            current_videos = self.load_videos_from_folder(self.recordings_dir)
            if len(current_videos) > self.settings["max_videos"]:
                 # Sort files by creation time to find the oldest
                 try:
                    all_files = [(f, os.path.getctime(os.path.join(self.recordings_dir, f)))
                                 for f in os.listdir(self.recordings_dir) if f.endswith(".mp4")]
                    all_files.sort(key=lambda x: x[1]) # Sort by timestamp (index 1)
                    if all_files:
                        oldest_video_filename = all_files[0][0] # Get filename of oldest
                        os.remove(os.path.join(self.recordings_dir, oldest_video_filename))
                        print(f"Removed oldest video: {oldest_video_filename}") # Optional log
                 except Exception as e:
                    print(f"Error pruning videos for camera {self.camera_id}: {e}")

    
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
        capture = self._get_or_init_capture() # Get/init camera first
        if not capture:
            # Optional: Yield an error image or just stop
            print(f"Cannot generate feed for camera {self.camera_id}: Capture device not available.")
            # You could yield a static "error" image frame here
            return
        while True:
            if not capture.isOpened(): # Check if capture is still valid
                 print(f"Error: Camera {self.camera_id} capture lost during feed generation.")
                 break # Exit the loop if camera fails

            ret, frame = capture.read() # Use the initialized capture object
            if not ret:
                print(f"Warning: Could not read frame from camera {self.camera_id} for feed.")
                # Decide behavior: break, continue, yield error frame?
                time.sleep(0.1) # Avoid busy-looping
                continue # Try reading next frame
            if self.recording:
                frame = cv2.rectangle(
                    frame,
                    (0, 0),
                    (frame.shape[1] - 1, frame.shape[0] - 1),
                    (0, 0, 255),
                    10,
                )
            _, buffer = cv2.imencode(".jpg", frame)
            frame = buffer.tobytes()
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")


# Initialize Flask app
app = Flask(__name__, template_folder='static/templates')
app.secret_key = '14a6a86bf47bf75c4479c0c70886b2a5'

# Initialize a single camera object

#camera1 = Camera("src/recordings/camera1", "src/incidents/camera1", "src/settings_camera1.json")
#camera2 = Camera("src/recordings/camera2", "src/incidents/camera2", "src/settings_camera2.json")
#cameras = {
    #"1": Camera("src/recordings/camera1", "src/incidents/camera1", "src/settings_camera1.json"),
    #"2": Camera("src/recordings/camera2", "src/incidents/camera2", "src/settings_camera2.json"),
#}


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
            # Optional: Check if it's actually a Camera instance
            if isinstance(camera, Camera):
                try:
                    # Call the release method we added earlier
                    camera.release_capture()
                except Exception as e:
                    # Log potential errors during release but continue
                    print(f"Error releasing camera {camera_id} during logout: {e}")
        print("Finished attempting camera releases for logout.")
    else:
        print("No 'cameras' dictionary found or it's empty during logout.")
    # --- End Camera Release ---
    
    session.clear()
    return redirect(url_for('login'))


# Flask routes and functions
@app.route('/videos/<camera_id>')
@login_required
def videos(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        return "Camera not found", 404
    videos_list = camera.load_videos_from_folder(camera.recordings_dir)
    return render_template("videos.html", videos=videos_list, camera_id=camera_id, username=session['username'],description=camera.description )

@app.route('/incident_videos/<camera_id>')
@login_required
def incident_videos(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        return "Camera not found", 404
    incident_videos = camera.load_incident_videos()
    return render_template("incident_vid.html", incident_videos=incident_videos, camera_id=camera_id, username=session['username'], description=camera.description)

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
    # Reload video information (dynamically triggered)
    return redirect(url_for("videos", camera_id=camera_id))

@app.route("/update_incident_info/<camera_id>", methods=["POST"])
def update_incident_info(camera_id):
    # Reload video information (dynamically triggered)
    return redirect(url_for("incident_videos", camera_id=camera_id))


@app.route('/start_recording/<camera_id>', methods=['POST'])
@login_required
def start_recording(camera_id):
    camera = cameras.get(camera_id)  # Get the camera instance from the dictionary
    if camera is None:
        return "Camera not found", 404  # Handle the case where the camera ID is invalid

    camera.recording = True
    Thread(target=camera.record_video).start()
    return "", 204

@app.route('/stop_recording/<camera_id>', methods=['POST'])
@login_required
def stop_recording(camera_id):
    camera = cameras.get(camera_id)  # Get the camera instance from the dictionary
    if camera is None:
        return "Camera not found", 404  # Handle the case where the camera ID is invalid

    camera.recording = False
    return "", 204

@app.route('/simulate_incident/<camera_id>', methods=['POST'])
@login_required
def simulate_incident(camera_id):
    camera = cameras.get(camera_id)  # Get the camera instance from the dictionary
    if camera is None:
        return "Camera not found", 404  # Handle the case where the camera ID is invalid

    camera.simulate_incident()
    return '', 204

@app.route("/video_feed/<camera_id>")
def video_feed(camera_id):
    camera = cameras.get(camera_id)  # Get the camera instance from the dictionary
    if camera is None:
        return "Camera not found", 404  # Handle the case where the camera ID is invalid

    return Response(camera.generate_video_feed(), mimetype="multipart/x-mixed-replace; boundary=frame")

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

atexit.register(release_all_cameras)

if __name__ == "__main__":
    load_cameras_from_json()
    app.run(debug=True)
        