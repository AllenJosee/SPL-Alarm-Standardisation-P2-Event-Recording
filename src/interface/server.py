from flask import Flask, render_template, Response, request, redirect, url_for, jsonify, session, send_from_directory
import os
import cv2
import json
from threading import Thread
import time
import shutil
from functools import wraps

recordings_dir = "src/recordings"
incidents_dir = "src/incidents"
settings_file = "src/settings.json"



class Camera:
    #device index
    def __init__(self, recordings_dir, incidents_dir, settings_file, device_index=0):
        self.recordings_dir = recordings_dir
        self.incidents_dir = incidents_dir
        self.settings_file = settings_file
        self.camera = cv2.VideoCapture(device_index)
        if not self.camera.isOpened():
            raise Exception("Could not open video device")        
        self.recording = False
        os.makedirs(recordings_dir, exist_ok=True)
        os.makedirs(incidents_dir, exist_ok=True)
        os.makedirs
        self.load_settings()

    def to_dict(self):
        return{
            "recordings_dir": self.recordings_dir,
            "incidents_dir": self.incidents_dir,
            "settings_file": self.settings_file,
        }
    
    @staticmethod
    def from_dict(data):
        return Camera(data["recordings_dir"], data["incidents_dir"], data["settings_file"])

    
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
        while self.recording:
            filename = time.strftime("%Y%m%d-%H%M%S") + ".mp4"
            filepath = os.path.join(self.recordings_dir, filename)
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            out = cv2.VideoWriter(filepath, fourcc, 15, (640, 480))
            start_time = time.time()
            frame_count = 0
            total_frames = int(15 * self.settings["video_duration"])
            while self.recording and frame_count < total_frames:
                ret, frame = self.camera.read()
                if ret:
                    out.write(frame)
                    frame_count += 1
                else:
                    break
            out.release()
            if len(self.load_videos_from_folder(self.recordings_dir)) > self.settings["max_videos"]:
                oldest_video = sorted(os.listdir(self.recordings_dir), key=lambda x: os.path.getctime(os.path.join(self.recordings_dir, x)))[0]
                os.remove(os.path.join(self.recordings_dir, oldest_video))
    
    def simulate_incident(self):
        incident_timestamp = time.strftime("%Y%m%d-%H%M%S")
        incident_folder = os.path.join(self.incidents_dir, incident_timestamp)
        os.makedirs(incident_folder, exist_ok=True)
        for video in reversed(self.load_videos_from_folder(self.recordings_dir)):
            shutil.copy(os.path.join(self.recordings_dir, video["filename"]), incident_folder)
            if len(os.listdir(incident_folder)) >= 6:
                break

    #Video Feed
    def generate_video_feed(self):
        while True:
            if not self.camera.isOpened():
                break 
            ret, frame = self.camera.read()
            if ret:
                # Add a red border if recording
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
    if os.path.exists('cameras.json'):
        with open('cameras.json', 'r') as f:
            camera_data = json.load(f)
            cameras = {camera_id: Camera.from_dict(data) for camera_id, data in camera_data.items()}


cameras = {}
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

    return render_template('index.html', username=session['username'], camera_id=camera_id)  # Pass camera_id to the template

@app.route('/camera_list')
@login_required
def camera_list():
    if not session.get('authenticated'):
        return redirect(url_for('login'))
    return render_template('camera_list.html', username=session['username'])

@app.route('/add_camera', methods=['GET', 'POST'])
@login_required
def add_camera():
    if request.method == 'POST':
        camera_id = request.form.get('camera_id')
        
        new_camera = Camera(
            f"src/recordings/camera{camera_id}",
            f"src/incidents/camera{camera_id}",
            f"src/settings_camera{camera_id}.json"
        )
        
        cameras[camera_id] = new_camera
        save_cameras_to_json()  # Save the updated cameras to JSON
        
        return redirect(url_for('camera_list'))
    
    return render_template('add_camera.html')

@app.route('/feed_view')
@login_required
def feed_view():
    if not session.get('authenticated'):
        return redirect(url_for('login'))
    return render_template('feed_view.html', username=session['username'])

@app.route('/logout', methods=['POST'])
def logout():
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
    return render_template("videos.html", videos=videos_list, camera_id=camera_id)

@app.route('/incident_videos/<camera_id>')
@login_required
def incident_videos(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        return "Camera not found", 404
    incident_videos = camera.load_incident_videos()
    return render_template("incident_vid.html", incident_videos=incident_videos, camera_id=camera_id)

@app.route('/settings_page/<camera_id>')
@login_required
def settings_page(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        return "Camera not found", 404

    # Assuming camera has attributes max_videos and video_duration
    return render_template('settings.html', camera_id=camera_id)

@app.route('/update_settings/<camera_id>', methods=['POST'])
@login_required
def update_settings(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        return "Camera not found", 404
    max_videos = request.form.get("max_videos", type=int)
    video_duration = request.form.get("video_duration", type=int)
    camera.update_settings(max_videos, video_duration)
    return redirect(url_for("index", camera_id=camera_id)) 

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


if __name__ == "__main__":
    load_cameras_from_json
    app.run(debug=True)
        