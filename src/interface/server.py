from flask import Flask, render_template, Response, request, redirect, url_for, jsonify,session
import os
import cv2
import json
from threading import Thread
import time
import shutil
import subprocess
import json
from functools import wraps

app = Flask(__name__, template_folder='static/templates')
app.secret_key = '14a6a86bf47bf75c4479c0c70886b2a4'

# Define directories
RECORDINGS_DIR = "src/recordings"
INCIDENTS_DIR = "src/incidents"
VIDEO_METADATA_FILE = "src/interface/src/videos.json"
SETTINGS_FILE = "src/settings.json"

# Create folders to save videos
if not os.path.exists(RECORDINGS_DIR):
    os.makedirs(RECORDINGS_DIR)
if not os.path.exists(INCIDENTS_DIR):
    os.makedirs(INCIDENTS_DIR)

# Default settings
default_settings = {"max_videos": 5, "video_duration": 5}

# Ensure settings file exists
if not os.path.exists(SETTINGS_FILE):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(default_settings, f)

# Load settings
with open(SETTINGS_FILE, "r") as f:
    settings = json.load(f)

# Ensure JSON file exists
if not os.path.exists(VIDEO_METADATA_FILE):
    with open(VIDEO_METADATA_FILE, "w") as f:
        json.dump([], f)

camera = cv2.VideoCapture(0)
recording = False
max_viedos = settings["max_videos"]
video_duration = settings["video_duration"]


# Load videos from folder
def load_videos_from_folder(folder):
    videos = []
    for file in os.listdir(folder):
        if file.endswith(".mp4"): #    if file.endswith(".avi"):
            filepath = os.path.join(folder, file)
            videos.append(
                {"filename": file, "timestamp": time.ctime(os.path.getctime(filepath))}
            )
    return videos


# Load incident videos dynamically
def load_incident_videos():
    incident_videos = []
    for folder in os.listdir(INCIDENTS_DIR):
        folder_path = os.path.join(INCIDENTS_DIR, folder)
        if os.path.isdir(folder_path):
            for file in os.listdir(folder_path):
                if file.endswith(".mp4"): #if file.endswith(".avi"):
                    file_path = os.path.join(folder_path, file)
                    incident_videos.append({"filename": file, "path": file_path})
    return incident_videos

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
        return redirect(url_for('index'))
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
            return redirect(url_for('index'))
        error = 'Invalid credentials. Please try again.'
    return render_template('login.html', error = error)

@app.route('/index')
@login_required
def index():
    if not session.get('authenticated'):
        return redirect(url_for('login'))
    return render_template('index.html', username=session['username'])


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))


#Normal pages
@app.route("/videos")
@login_required
def videos():
    # Load videos dynamically from folders
    videos_list = load_videos_from_folder(RECORDINGS_DIR)
    incident_videos = load_incident_videos()
    return render_template(
        "videos.html", videos=videos_list, incident_videos=incident_videos
    )

@app.route("/incident_videos")
@login_required
def incident_videos():
    incident_videos = load_incident_videos()
    return render_template("incident_vid.html", incident_videos=incident_videos)

 
@app.route("/settings_page")
@login_required
def settings_page():
    return render_template("settings.html")


@app.route("/update_settings", methods=["POST"])
def update_settings():
    global settings

    # Get new settings from form data
    max_videos = request.form.get("max_videos", type=int)
    video_duration = request.form.get("video_duration", type=int)

    # Debugging statements
    print(f"Received max_videos: {max_videos}")
    print(f"Received video_duration: {video_duration}")

    # Update settings
    if max_videos is not None:
        settings["max_videos"] = max_videos
    if video_duration is not None:
        settings["video_duration"] = video_duration

    # Save updated settings to file
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)
    
    # Debugging statement
    print(f"Updated settings: {settings}")

    return redirect(url_for("index"))


@app.route("/update_info", methods=["POST"])
def update_info():
    # Reload video information (dynamically triggered)
    return redirect(url_for("videos"))

@app.route("/update_incident_info", methods=["POST"])
def update_inicident_info():
    # Reload video information (dynamically triggered)
    return redirect(url_for("incident_videos"))

@app.route("/download/<filename>")
def download_video(filename):
    return redirect(url_for("static", filename=f"recordings/{filename}"))


@app.route("/simulate_incident", methods=["POST"])
def simulate_incident():
    incident_timestamp = time.strftime("%Y%m%d-%H%M%S")
    incident_folder = os.path.join(INCIDENTS_DIR, incident_timestamp)
    os.makedirs(incident_folder, exist_ok=True)

    # Copy relevant videos to the incident folder
    for video in reversed(load_videos_from_folder(RECORDINGS_DIR)):
        shutil.copy(os.path.join(RECORDINGS_DIR, video["filename"]), incident_folder)
        if len(os.listdir(incident_folder)) >= 6:  # Limit to 6 videos
            break
    return redirect(url_for("incident_videos"))


@app.route("/video_feed")
def video_feed():
    def generate():
        global recording
        while True:
            ret, frame = camera.read()
            if ret:
                # Add a red border if recording
                if recording:
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

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/start_recording", methods=["POST"])
def start_recording():
    global recording
    recording = True
    Thread(target=record_video).start()
    return "", 204


@app.route("/stop_recording", methods=["POST"])
def stop_recording():
    global recording
    recording = False
    return "", 204


def record_video():
    global recording

    # Load settings from JSON file
    with open(SETTINGS_FILE, "r") as f:
        settings = json.load(f)
    
    while recording:
        
        video_duration = settings["video_duration"]
        max_videos = settings["max_videos"]

        filename = time.strftime("%Y%m%d-%H%M%S") + ".mp4" #filename = time.strftime("%Y%m%d-%H%M%S") + ".avi"
        filepath = os.path.join(RECORDINGS_DIR, filename)

        fourcc = cv2.VideoWriter_fourcc(*"mp4v") #fourcc = cv2.VideoWriter_fourcc(*"XVID")
        actual_fps = 15
        out = cv2.VideoWriter(filepath, fourcc, actual_fps, (640, 480))
        start_time = time.time()

        frame_count = 0
        total_frames = int(actual_fps * video_duration)
        while recording and frame_count < total_frames:
            ret, frame = camera.read()
            if ret:
                out.write(frame)
                frame_count += 1
            else:
                break

        out.release()
        
        # Ensure recording count doesn't exceed the limit
        if len(load_videos_from_folder(RECORDINGS_DIR)) > max_videos:
            oldest_video = sorted(
                os.listdir(RECORDINGS_DIR),
                key=lambda x: os.path.getctime(os.path.join(RECORDINGS_DIR, x)),
            )[0]
            os.remove(os.path.join(RECORDINGS_DIR, oldest_video))


if __name__ == "__main__":
    app.run(debug=True)
