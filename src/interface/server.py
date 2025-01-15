from flask import Flask, render_template, Response, request, redirect, url_for, jsonify
import os
import cv2
import json
from threading import Thread
import time
import shutil

app = Flask(__name__)

# Define directories
RECORDINGS_DIR = "src/recordings"
INCIDENTS_DIR = "src/recordings/incidents"
VIDEO_METADATA_FILE = "src/videos.json"

# Create folders to save videos
if not os.path.exists(RECORDINGS_DIR):
    os.makedirs(RECORDINGS_DIR)
if not os.path.exists(INCIDENTS_DIR):
    os.makedirs(INCIDENTS_DIR)

# Ensure JSON file exists
if not os.path.exists(VIDEO_METADATA_FILE):
    with open(VIDEO_METADATA_FILE, "w") as f:
        json.dump([], f)

camera = cv2.VideoCapture(0)
recording = False
max_videos = 5
video_duration = 5  # default duration in seconds


# Load videos from folder
def load_videos_from_folder(folder):
    videos = []
    for file in os.listdir(folder):
        if file.endswith(".avi"):
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
                if file.endswith(".avi"):
                    file_path = os.path.join(folder_path, file)
                    incident_videos.append({"filename": file, "path": file_path})
    return incident_videos


@app.route("/")
def index():
    return render_template(
        "index.html", max_videos=max_videos, video_duration=video_duration
    )


@app.route("/videos")
def videos():
    # Load videos dynamically from folders
    videos_list = load_videos_from_folder(RECORDINGS_DIR)
    incident_videos = load_incident_videos()
    return render_template(
        "videos.html", videos=videos_list, incident_videos=incident_videos
    )


@app.route("/update_settings", methods=["POST"])
def update_settings():
    global max_videos, video_duration
    max_videos = int(request.form["max_videos"])
    video_duration = int(request.form["video_duration"])
    return redirect(url_for("index"))


@app.route("/update_info", methods=["POST"])
def update_info():
    # Reload video information (dynamically triggered)
    return redirect(url_for("videos"))


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

    return redirect(url_for("videos"))


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
    while recording:
        filename = time.strftime("%Y%m%d-%H%M%S") + ".avi"
        filepath = os.path.join(RECORDINGS_DIR, filename)

        fourcc = cv2.VideoWriter_fourcc(*"XVID")
        out = cv2.VideoWriter(filepath, fourcc, 20.0, (640, 480))
        start_time = time.time()

        while recording and (time.time() - start_time) < video_duration:
            ret, frame = camera.read()
            if ret:
                out.write(frame)
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
