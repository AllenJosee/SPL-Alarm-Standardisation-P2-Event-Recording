#Same functionality as server2.py
#Auto-recording is enabled for camera 1, as soon as the program starts.
#video savings, and pruning follow the camera1.json settings.
#Camera 1 will auto record before the log in, user can navigate to the details page as usual.

# 25/6/25 UPDATED: Now with database integration and sensor info page from server2_db.py

from flask import Flask, render_template, Response, request, redirect, url_for, jsonify, session, send_from_directory, flash, g
import os
import cv2
import json
from threading import Thread, Event, Lock
import time
import shutil
from functools import wraps
import atexit
import sqlite3 
import logging 
import datetime 
from dateutil import parser 

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO) # Basic logging for the script
# Initialize Flask app
app = Flask(__name__, template_folder='static/templates')
app.secret_key = '14a6a86bf47bf75c4479c0c70886b2a5'
app.logger.setLevel(logging.INFO) # Set Flask app logger level

# --- Path Configurations ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT_DIR = os.path.dirname(SRC_DIR)

SENSOR_DATA_FILE = os.path.join(PROJECT_ROOT_DIR, 'sensor_data.json') 

# --- Database Configuration ---
DATABASE_NAME = 'videos.db'
TABLE_NAME = 'video_metadata'
INCIDENT_TABLE_NAME = 'incident_video_metadata'
DATABASE_PATH = os.path.join(PROJECT_ROOT_DIR, DATABASE_NAME) 

# --- Flask Database Helper Functions ---
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE_PATH)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE
        )
    ''')
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS {INCIDENT_TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            incident_trigger_timestamp TEXT NOT NULL,
            original_video_filename TEXT NOT NULL,
            original_video_timestamp TEXT,
            incident_folder_name TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE
        )
    ''')
    conn.commit()
    conn.close()
    app.logger.info(f"Database {DATABASE_NAME} initialized with tables: {TABLE_NAME}, {INCIDENT_TABLE_NAME}")

init_db() # Initialize DB at startup

# --- Sensor Data Functions ---
def load_sensor_data():
    if not os.path.exists(SENSOR_DATA_FILE):
        return {}
    try:
        with open(SENSOR_DATA_FILE, 'r') as f:
            data = json.load(f)
            return data
    except json.JSONDecodeError:
        app.logger.error(f"Error decoding JSON from {SENSOR_DATA_FILE}. Returning empty data.")
        return {}
    except Exception as e:
        app.logger.error(f"Error loading sensor data from {SENSOR_DATA_FILE}: {e}")
        return {}

def save_sensor_data(data):
    try:
        with open(SENSOR_DATA_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        app.logger.info(f"Sensor data saved to {SENSOR_DATA_FILE}")
        return True
    except Exception as e:
        app.logger.error(f"Error saving sensor data to {SENSOR_DATA_FILE}: {e}")
        return False

# --- Timestamp Formatting ---
def format_display_timestamp_sgt(iso_timestamp_str_utc):
    if not iso_timestamp_str_utc:
        return "N/A"
    try:
        dt_object_utc = parser.isoparse(iso_timestamp_str_utc)
        if dt_object_utc.tzinfo is None or dt_object_utc.tzinfo.utcoffset(dt_object_utc) is None:
            dt_object_utc = dt_object_utc.replace(tzinfo=datetime.timezone.utc)
        else:
            dt_object_utc = dt_object_utc.astimezone(datetime.timezone.utc)
        sgt_timezone = datetime.timezone(datetime.timedelta(hours=8))
        dt_object_sgt = dt_object_utc.astimezone(sgt_timezone)
        return dt_object_sgt.strftime("%Y-%m-%d %H:%M:%S SGT")
    except Exception as e:
        app.logger.warning(f"Could not parse/convert timestamp: '{iso_timestamp_str_utc}'. Error: {e}")
        return iso_timestamp_str_utc

class Camera:
    def __init__(self, camera_id, recordings_dir, incidents_dir, settings_file, description="", device_index=0):
        self.camera_id = str(camera_id)
        self.recordings_dir = recordings_dir # Relative to src, e.g., "recordings/camera1"
        self.incidents_dir = incidents_dir   # Relative to src, e.g., "incidents/camera1"
        self.settings_file = settings_file   # Relative to src, e.g., "settings_camera1.json"
        self.description = description
        self.device_index = device_index

        self.capture = None
        self.capture_lock = Lock()
        self.active_feed_clients = 0

        self.recording = False
        self._stop_recording_event = Event()
        self.recording_thread = None

        os.makedirs(os.path.join(PROJECT_ROOT_DIR, self.recordings_dir), exist_ok=True)
        os.makedirs(os.path.join(PROJECT_ROOT_DIR, self.incidents_dir), exist_ok=True)
        self.load_settings()


    def _ensure_capture_initialized(self):
        with self.capture_lock:
            if self.capture is None or not self.capture.isOpened():
                if self.capture is not None:
                    app.logger.info(f"Releasing stale capture for camera {self.camera_id} before re-initializing.")
                    self.capture.release()
                    self.capture = None
                try:
                    app.logger.info(f"Initializing cv2.VideoCapture for camera {self.camera_id} (device: {self.device_index})...")
                    self.capture = cv2.VideoCapture(self.device_index)
                    if not self.capture.isOpened():
                        app.logger.error(f"Error: Could not open video device {self.device_index} for camera {self.camera_id}")
                        self.capture = None
                    else:
                        app.logger.info(f"Successfully opened camera {self.camera_id}.")
                except Exception as e:
                    app.logger.error(f"Exception initializing cv2.VideoCapture for camera {self.camera_id}: {e}")
                    self.capture = None
            return self.capture

    def _release_capture_if_unused(self):
        with self.capture_lock:
            if self.capture is not None and self.capture.isOpened():
                if not self.recording and self.active_feed_clients == 0:
                    app.logger.info(f"Releasing capture for camera {self.camera_id} (no recording, no feed clients).")
                    self.capture.release()
                    self.capture = None
                else:
                    app.logger.info(f"Capture for camera {self.camera_id} NOT released. Recording: {self.recording}, Feed Clients: {self.active_feed_clients}")

    def release_capture(self):
        app.logger.info(f"FULL release_capture called for camera {self.camera_id}")
        if self.recording:
            self.stop_recording_logic(called_from_release_all=True)
        with self.capture_lock:
            if self.capture is not None and self.capture.isOpened():
                app.logger.info(f"Force releasing capture for camera {self.camera_id} during full shutdown.")
                self.capture.release()
                self.capture = None
        self.active_feed_clients = 0
        app.logger.info(f"All resources for camera {self.camera_id} should be released after full shutdown sequence.")

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
        # settings_file is relative to src, e.g., "src/settings_camera1.json"
        # Needs to be absolute for file operations
        abs_settings_file_path = os.path.join(PROJECT_ROOT_DIR, self.settings_file)
        if not os.path.exists(abs_settings_file_path):
            default_settings = {
                "max_videos": 5,
                "video_duration": 5,
            }
            with open(abs_settings_file_path, "w") as f:
                json.dump(default_settings, f)
        with open(abs_settings_file_path, "r") as f:
            self.settings = json.load(f)

    def update_settings(self, max_videos, video_duration):
        self.settings["max_videos"] = max_videos
        self.settings["video_duration"] = video_duration
        abs_settings_file_path = os.path.join(PROJECT_ROOT_DIR, self.settings_file)
        with open(abs_settings_file_path, "w") as f:
            json.dump(self.settings, f)
        return True 

    def start_recording_thread(self):
        if self.recording and self.recording_thread and self.recording_thread.is_alive():
            app.logger.info(f"Camera {self.camera_id} is already recording.")
            return self.recording_thread

        app.logger.info(f"Attempting to start recording thread for camera {self.camera_id}...")
        if self._ensure_capture_initialized():
            self.recording = True
            self._stop_recording_event.clear()
            if self.recording_thread and not self.recording_thread.is_alive():
                self.recording_thread = None
            if self.recording_thread and self.recording_thread.is_alive():
                app.logger.warning(f"Warning: Previous recording thread for {self.camera_id} still alive. Joining...")
                self.recording_thread.join(timeout=1.0)

            self.recording_thread = Thread(target=self.record_video, name=f"RecordThread-{self.camera_id}")
            self.recording_thread.daemon = True
            self.recording_thread.start()
            app.logger.info(f"Recording thread started for camera {self.camera_id}.")
            return self.recording_thread
        else:
            app.logger.error(f"Failed to initialize capture for recording on camera {self.camera_id}. Recording not started.")
            self.recording = False
            return None

    def stop_recording_logic(self, called_from_release_all=False):
        app.logger.info(f"stop_recording_logic called for camera {self.camera_id}. From release_all: {called_from_release_all}")
        if not self.recording and not (self.recording_thread and self.recording_thread.is_alive()):
            app.logger.info(f"Camera {self.camera_id} not actively recording or thread inactive.")
            if not called_from_release_all:
                self._release_capture_if_unused()
            return

        self.recording = False
        self._stop_recording_event.set()

        if self.recording_thread and self.recording_thread.is_alive():
            app.logger.info(f"Waiting for recording thread {self.camera_id} to finish...")
            self.recording_thread.join(timeout=5.0) 
            if self.recording_thread.is_alive():
                app.logger.warning(f"Warning: Recording thread {self.camera_id} did not exit cleanly after 5 seconds.")
            else:
                app.logger.info(f"Recording thread {self.camera_id} joined successfully.")
        self.recording_thread = None
        app.logger.info(f"Recording thread for {self.camera_id} stopped/joined.")

        if not called_from_release_all:
            self._release_capture_if_unused()
        app.logger.info(f"Recording stopped for camera {self.camera_id}.")

    def record_video(self):
        app.logger.info(f"Record_video loop started for camera {self.camera_id}.")
        while self.recording and not self._stop_recording_event.is_set():
            db_timestamp_to_store = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
            filename_timestamp_part = time.strftime("%Y%m%d-%H%M%S")
            filename = f"cam{str(self.camera_id)}_{filename_timestamp_part}.mp4"
            
            # path for DB should be relative to PROJECT_ROOT
            relative_path_for_db = os.path.join(self.recordings_dir, filename)
            absolute_filepath_for_cv = os.path.join(PROJECT_ROOT_DIR, relative_path_for_db)
            
            # Ensure directory for this specific camera exists
            os.makedirs(os.path.dirname(absolute_filepath_for_cv), exist_ok=True)

            fourcc = cv2.VideoWriter_fourcc(*"avc1")
            if not hasattr(self, 'settings') or not self.settings: self.load_settings()
            video_duration_seconds = self.settings.get("video_duration", 5)
            fps = 15
            out = None

            try:
                with self.capture_lock:
                    if not self.capture or not self.capture.isOpened():
                        app.logger.error(f"Cam {self.camera_id}: SHARED capture became unopened. Stopping recording.")
                        self.recording = False; break
                    frame_width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
                    frame_height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    if frame_width == 0 or frame_height == 0:
                        app.logger.error(f"Cam {self.camera_id}: Invalid frame dimensions ({frame_width}x{frame_height}). Skipping segment.")
                        time.sleep(1); continue
                
                app.logger.info(f"Cam {self.camera_id}: Writing to {absolute_filepath_for_cv} ({frame_width}x{frame_height}@{fps}fps)")
                out = cv2.VideoWriter(absolute_filepath_for_cv, fourcc, fps, (frame_width, frame_height))
                if not out.isOpened():
                    app.logger.error(f"Cam {self.camera_id}: Failed to open VideoWriter for {absolute_filepath_for_cv}"); time.sleep(1); continue
            except Exception as e:
                app.logger.error(f"Cam {self.camera_id}: Error creating VideoWriter: {e}"); time.sleep(1); continue

            if out and out.isOpened():
                frame_count = 0
                total_frames_to_record = int(fps * video_duration_seconds)
                while self.recording and not self._stop_recording_event.is_set() and frame_count < total_frames_to_record:
                    frame_read_successfully = False; frame_data = None
                    with self.capture_lock:
                        if not self.capture or not self.capture.isOpened():
                            app.logger.error(f"Cam {self.camera_id}: SHARED capture lost during segment write.")
                            self.recording = False; break
                        ret, current_frame = self.capture.read()
                        if ret: frame_data = current_frame; frame_read_successfully = True
                    
                    if self._stop_recording_event.is_set(): break
                    if frame_read_successfully and frame_data is not None:
                        out.write(frame_data); frame_count += 1
                    elif self.recording:
                        app.logger.warning(f"Cam {self.camera_id}: Failed to read frame from shared capture during RECORDING segment."); time.sleep(0.05)
                
                if not self.recording or self._stop_recording_event.is_set():
                    if out.isOpened(): out.release(); break
                
                out.release()
                app.logger.info(f"Cam {self.camera_id}: Wrote {frame_count} frames to {filename}.")

                if frame_count > 0:
                    self.insert_video_metadata(filename, db_timestamp_to_store, relative_path_for_db)
                    self.prune_videos_from_database()
                else:
                    app.logger.warning(f"Cam {self.camera_id}: No frames recorded for {filename}. Not saving to DB or pruning.")
                    if os.path.exists(absolute_filepath_for_cv):
                        try: os.remove(absolute_filepath_for_cv)
                        except OSError as e_del: app.logger.error(f"Cam {self.camera_id}: Error removing empty file: {e_del}")
            
            if self.recording and not self._stop_recording_event.is_set():
                time.sleep(0.01)
        app.logger.info(f"Record_video loop EXITED for camera {self.camera_id}.")

    def insert_video_metadata(self, filename, timestamp_str, relative_path_to_project_root):
        conn = None
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            query = f"INSERT INTO {TABLE_NAME} (camera_id, filename, timestamp, path) VALUES (?, ?, ?, ?)"
            cursor.execute(query, (str(self.camera_id), filename, timestamp_str, relative_path_to_project_root))
            conn.commit()
            app.logger.debug(f"DB Insert Cam {self.camera_id}: File={filename}, Path={relative_path_to_project_root}")
        except sqlite3.Error as e:
            app.logger.error(f"DB Error Cam {self.camera_id} inserting {filename}: {e}")
            if conn: conn.rollback()
        finally:
            if conn: conn.close()

    def prune_videos_from_database(self):
        conn = None
        try:
            if not hasattr(self, 'settings') or not self.settings: self.load_settings()
            max_videos = self.settings.get("max_videos", 5)
            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            query_select = f"SELECT id, path FROM {TABLE_NAME} WHERE camera_id = ? ORDER BY timestamp ASC"
            cursor.execute(query_select, (str(self.camera_id),))
            all_db_videos = cursor.fetchall()
            if len(all_db_videos) > max_videos:
                num_to_delete = len(all_db_videos) - max_videos
                videos_to_delete = all_db_videos[:num_to_delete]
                app.logger.info(f"[PruneDB Cam {self.camera_id}] Deleting {len(videos_to_delete)} oldest video(s).")
                for video_entry in videos_to_delete:
                    abs_path_to_delete = os.path.join(PROJECT_ROOT_DIR, video_entry["path"])
                    if os.path.exists(abs_path_to_delete):
                        try: os.remove(abs_path_to_delete)
                        except OSError as e: app.logger.error(f"Error deleting file {abs_path_to_delete}: {e}")
                    cursor.execute(f"DELETE FROM {TABLE_NAME} WHERE id = ?", (video_entry["id"],))
                conn.commit()
        except sqlite3.Error as e:
            if conn: conn.rollback()
            app.logger.error(f"[PruneDB Cam {self.camera_id}] SQLite Error: {e}")
        except Exception as e:
            app.logger.error(f"[PruneDB Cam {self.camera_id}] General Error: {e}")
        finally:
            if conn: conn.close()
            
    def load_videos_from_database(self): # For simulate_incident to get source videos
        conn = None; videos = []
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # Get newest videos first, limit if necessary for simulate_incident
            query = f"SELECT id, filename, timestamp, path FROM {TABLE_NAME} WHERE camera_id = ? ORDER BY timestamp DESC LIMIT 6"
            cursor.execute(query, (str(self.camera_id),))
            videos = cursor.fetchall() # Returns list of Row objects
        except sqlite3.Error as e:
            app.logger.error(f"[Cam {self.camera_id}] DB error loading videos for incident: {e}")
        finally:
            if conn: conn.close()
        return videos


    def insert_incident_video_metadata(self, incident_trigger_ts, original_filename, original_ts, incident_folder, copied_video_relative_path):
        conn = None
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            query = f"INSERT INTO {INCIDENT_TABLE_NAME} (camera_id, incident_trigger_timestamp, original_video_filename, original_video_timestamp, incident_folder_name, path) VALUES (?, ?, ?, ?, ?, ?)"
            cursor.execute(query, (str(self.camera_id), incident_trigger_ts, original_filename, original_ts, incident_folder, copied_video_relative_path))
            conn.commit()
        except sqlite3.Error as e:
            app.logger.error(f"DB Error Cam {self.camera_id} inserting incident {original_filename}: {e}")
            if conn: conn.rollback()
        finally:
            if conn: conn.close()

    def simulate_incident(self):
        incident_trigger_iso_ts = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
        incident_folder_ts_part = time.strftime("%Y%m%d-%H%M%S")
        incident_folder_name_only = f"cam{str(self.camera_id)}_incident_{incident_folder_ts_part}"
        
        # self.incidents_dir is relative to src, e.g., "src/incidents/camera1"
        # relative_incident_folder_path is like "src/incidents/camera1/cam1_incident_XYZ"
        relative_incident_folder_path = os.path.join(self.incidents_dir, incident_folder_name_only)
        absolute_incident_folder_path = os.path.join(PROJECT_ROOT_DIR, relative_incident_folder_path)
        
        os.makedirs(absolute_incident_folder_path, exist_ok=True)
        app.logger.info(f"Cam {self.camera_id}: Created incident folder {absolute_incident_folder_path}")

        videos_from_db = self.load_videos_from_database() # Gets up to 6 newest normal recordings
        
        copied_count = 0
        for video_data_row in videos_from_db: # video_data_row is a sqlite3.Row object
            original_relative_path = video_data_row["path"] # e.g., "src/recordings/camera1/video.mp4"
            original_filename = video_data_row["filename"]
            original_timestamp_from_db = video_data_row["timestamp"]

            source_absolute_path = os.path.join(PROJECT_ROOT_DIR, original_relative_path)
            
            relative_copied_video_path = os.path.join(relative_incident_folder_path, original_filename)
            destination_absolute_path = os.path.join(PROJECT_ROOT_DIR, relative_copied_video_path)

            if os.path.exists(source_absolute_path):
                try:
                    shutil.copy(source_absolute_path, destination_absolute_path)
                    app.logger.info(f"Incident Cam {self.camera_id}: Copied {original_filename} to {incident_folder_name_only}")
                    self.insert_incident_video_metadata(
                        incident_trigger_iso_ts, original_filename, original_timestamp_from_db,
                        incident_folder_name_only, relative_copied_video_path
                    )
                    copied_count += 1
                    if copied_count >= 6: break # Ensure max 6
                except Exception as e:
                    app.logger.error(f"Incident Cam {self.camera_id}: Error copying/logging {original_filename}: {e}")
            else:
                app.logger.warning(f"Incident Cam {self.camera_id}: Source file {source_absolute_path} missing.")


    def generate_video_feed(self):
        app.logger.info(f"Attempting to generate live feed for camera {self.camera_id}")
        local_capture_ref = self._ensure_capture_initialized()
        if not local_capture_ref or not local_capture_ref.isOpened():
            app.logger.error(f"Error: Could not get/initialize shared capture for LIVE FEED on camera {self.camera_id}")
            return
        with self.capture_lock: self.active_feed_clients += 1
        app.logger.info(f"Successfully using shared capture for LIVE FEED on camera {self.camera_id}. Active clients: {self.active_feed_clients}")

        try:
            while True:
                frame = None; frame_available = False
                with self.capture_lock:
                    if not self.capture or not self.capture.isOpened():
                        app.logger.error(f"Error: Camera {self.camera_id} SHARED capture lost during LIVE FEED.")
                        break
                    ret, frame_data = self.capture.read()
                    if ret: frame = frame_data.copy(); frame_available = True
                
                if not frame_available:
                    app.logger.warning(f"Warning: Could not read frame from camera {self.camera_id} for LIVE FEED."); time.sleep(0.1); continue

                if self.recording:
                    cv2.rectangle(frame, (0, 0), (frame.shape[1] - 1, frame.shape[0] - 1), (0, 0, 255), 10)

                _, buffer = cv2.imencode(".jpg", frame)
                if buffer is None: app.logger.warning(f"Warning: cv2.imencode failed for camera {self.camera_id}"); continue
                frame_bytes = buffer.tobytes()
                try:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")
                except GeneratorExit: app.logger.info(f"Client disconnected from live feed for camera {self.camera_id}."); break
                except Exception as e_yield: app.logger.error(f"Error yielding frame for camera {self.camera_id}: {e_yield}"); break
                time.sleep(1/30) # Approx 30fps
        except Exception as e_feed:
            app.logger.error(f"Exception in generate_video_feed for camera {self.camera_id}: {e_feed}")
        finally:
            with self.capture_lock: self.active_feed_clients -= 1
            app.logger.info(f"Live feed generation ended for camera {self.camera_id}. Active clients: {self.active_feed_clients}")
            self._release_capture_if_unused()

# --- Global Camera Management ---
cameras = {}
auto_record_camera_id = "1" # Camera ID to auto-record
# auto_record_camera will be assigned in load_cameras_from_json

def save_cameras_to_json():
    camera_data = {cam_id: cam.to_dict() for cam_id, cam in cameras.items()}
    abs_cameras_json_path = os.path.join(PROJECT_ROOT_DIR, 'cameras.json') # Path relative to project root
    with open(abs_cameras_json_path, 'w') as f:
        json.dump(camera_data, f, indent=4)
    app.logger.info("Cameras saved to cameras.json")

def load_cameras_from_json():
    global cameras, auto_record_camera_id, auto_record_camera # auto_record_camera added to global
    cameras = {}
    auto_record_camera = None # Initialize here
    abs_cameras_json_path = os.path.join(PROJECT_ROOT_DIR, 'cameras.json')

    if os.path.exists(abs_cameras_json_path):
        try:
            with open(abs_cameras_json_path, 'r') as f:
                loaded_data = json.load(f)
                for cam_id_key, camera_config_data in loaded_data.items():
                    if 'camera_id' not in camera_config_data:
                        camera_config_data['camera_id'] = cam_id_key
                    
                    # Ensure paths in camera_config_data are relative to src/ as expected by Camera class
                    for path_key in ['recordings_dir', 'incidents_dir', 'settings_file']:
                        if path_key in camera_config_data:
                            pass 

                    camera_obj = Camera.from_dict(camera_config_data)
                    cameras[cam_id_key] = camera_obj
                    if cam_id_key == auto_record_camera_id:
                        auto_record_camera = camera_obj
                        app.logger.info(f"Designated auto-record camera '{auto_record_camera_id}' found.")
        except Exception as e:
            app.logger.error(f"Error loading cameras from {abs_cameras_json_path}: {e}")

    if auto_record_camera is None and auto_record_camera_id not in cameras:
        app.logger.warning(f"Auto-record camera '{auto_record_camera_id}' not found. Creating default.")
        default_rec_dir = f"src/recordings/camera{auto_record_camera_id}"
        default_inc_dir = f"src/incidents/camera{auto_record_camera_id}"
        default_set_file = f"src/settings_camera{auto_record_camera_id}.json"
        try:
            auto_record_camera = Camera(
                camera_id=auto_record_camera_id,
                recordings_dir=default_rec_dir,
                incidents_dir=default_inc_dir,
                settings_file=default_set_file,
                description="Default Auto-Record Camera", device_index=0
            )
            cameras[auto_record_camera_id] = auto_record_camera
            save_cameras_to_json() # Save if a new default is created
            app.logger.info(f"Default auto-record camera '{auto_record_camera_id}' created and saved.")
        except Exception as e:
            app.logger.error(f"Error creating default auto-record camera: {e}")
            auto_record_camera = None

load_cameras_from_json()

# --- User Authentication ---
def validate_user(username, password):
    users_path = os.path.join(SCRIPT_DIR, 'users.json') # users.json in the same dir as this script
    try:
        if not os.path.exists(users_path):
            raise FileNotFoundError(f"users.json not found at {users_path}")
        with open(users_path) as f: data = json.load(f)
        users = data.get('users', [])
        return any(user.get('username') == username.strip() and user.get('password') == password.strip() for user in users)
    except Exception as e: app.logger.error(f"Auth Error: {str(e)}"); return False

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'): return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Flask Routes ---
@app.route("/", methods=['GET'])
def home():
    if 'authenticated' in session: return redirect(url_for('camera_list'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        if validate_user(username, password):
            session['authenticated'] = True; session['username'] = username
            return redirect(url_for('camera_list'))
        error = 'Invalid credentials. Please try again.'
    return render_template('login.html', error=error)

@app.route('/index/<camera_id>')
@login_required
def index(camera_id):
    camera = cameras.get(camera_id)
    if camera is None: return "Camera not found", 404
    recording_status = 'recording' if camera.recording else 'stopped' # Get current status
    return render_template('index.html', username=session['username'], camera_id=camera_id, description=camera.description, recording_status=recording_status)

@app.route('/camera_list')
@login_required
def camera_list():
    return render_template('camera_list.html', username=session['username'], cameras=cameras)

@app.route('/add_camera', methods=['GET', 'POST'])
@login_required
def add_camera():
    error = None
    if request.method == 'POST':
        description = request.form.get('description', '').strip()
        next_id_int = 1
        if cameras:
            numeric_ids = [int(k) for k in cameras.keys() if k.isdigit()]
            if numeric_ids: next_id_int = max(numeric_ids) + 1
        new_camera_id_str = str(next_id_int)
        try:
            new_camera = Camera(
                camera_id=new_camera_id_str,
                recordings_dir=f"src/recordings/camera{new_camera_id_str}", # Path relative to PROJECT_ROOT
                incidents_dir=f"src/incidents/camera{new_camera_id_str}",   # Path relative to PROJECT_ROOT
                settings_file=f"src/settings_camera{new_camera_id_str}.json",# Path relative to PROJECT_ROOT
                description=description
            )
            cameras[new_camera_id_str] = new_camera
            save_cameras_to_json()
            return redirect(url_for('camera_list'))
        except Exception as e:
            app.logger.error(f"Error creating camera {new_camera_id_str}: {e}")
            error = "An unexpected error occurred."
    return render_template('add_camera.html', error=error)

@app.route('/delete_camera/<camera_id>', methods=['POST'])
@login_required
def delete_camera(camera_id):
    if camera_id in cameras:
        camera_to_delete = cameras[camera_id]
        camera_to_delete.release_capture()

        # Paths are relative to PROJECT_ROOT as stored in Camera object after modification
        abs_recordings_path = os.path.join(PROJECT_ROOT_DIR, camera_to_delete.recordings_dir)
        abs_incidents_path = os.path.join(PROJECT_ROOT_DIR, camera_to_delete.incidents_dir)
        abs_settings_path = os.path.join(PROJECT_ROOT_DIR, camera_to_delete.settings_file)
        
        camera_id_str = str(camera_to_delete.camera_id)

        try:
            del cameras[camera_id_str]
            save_cameras_to_json()
            if os.path.exists(abs_recordings_path): shutil.rmtree(abs_recordings_path)
            if os.path.exists(abs_incidents_path): shutil.rmtree(abs_incidents_path)
            if os.path.exists(abs_settings_path): os.remove(abs_settings_path)

            # Delete from DB
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM {TABLE_NAME} WHERE camera_id = ?", (camera_id_str,))
            cursor.execute(f"DELETE FROM {INCIDENT_TABLE_NAME} WHERE camera_id = ?", (camera_id_str,))
            conn.commit()
            
            # Delete from sensor_data.json
            all_sensor_data = load_sensor_data()
            if camera_id_str in all_sensor_data:
                del all_sensor_data[camera_id_str]
                save_sensor_data(all_sensor_data)
            
            flash(f"Camera {camera_id_str} and all data deleted.", "success")
        except Exception as e:
            app.logger.error(f"Error deleting camera {camera_id_str}: {e}")
            flash(f"Error deleting camera {camera_id_str}.", "error")
        return redirect(url_for('camera_list'))
    flash(f"Camera {camera_id} not found.", "warning")
    return redirect(url_for('camera_list'))


@app.route('/feed_view')
@login_required
def feed_view():
    return render_template('feed_view.html', username=session['username'])

@app.route('/logout', methods=['POST'])
def logout():
    # Auto-recording camera continues to record on logout
    session.clear()
    return redirect(url_for('login'))

# Whitelist for sort columns
ALLOWED_SORT_COLUMNS_VIDEOS = {'id': 'id', 'filename': 'filename', 'timestamp': 'timestamp'}
ALLOWED_SORT_COLUMNS_INCIDENTS = {'id': 'id', 'filename': 'original_video_filename', 'folder': 'incident_folder_name', 'timestamp': 'incident_trigger_timestamp'}

@app.route('/videos/<camera_id>')
@login_required
def videos(camera_id):
    camera = cameras.get(camera_id)
    if camera is None: flash(f"Camera {camera_id} not found.", "error"); return redirect(url_for('camera_list'))
    
    sort_by = request.args.get('sort_by', 'timestamp')
    sort_order = request.args.get('sort_order', 'desc')
    db_col = ALLOWED_SORT_COLUMNS_VIDEOS.get(sort_by, 'timestamp')
    sql_order = 'DESC' if sort_order == 'desc' else 'ASC'

    conn = get_db(); cursor = conn.cursor()
    query = f"SELECT id, filename, timestamp, path FROM {TABLE_NAME} WHERE camera_id = ? ORDER BY {db_col} {sql_order}"
    videos_for_template = []
    try:
        cursor.execute(query, (str(camera_id),))
        for row in cursor.fetchall():
            videos_for_template.append({
                "id": row["id"], "filename": row["filename"],
                "timestamp": row["timestamp"], # Raw ISO
                "display_timestamp": format_display_timestamp_sgt(row["timestamp"]),
                "path": row["path"]
            })
    except sqlite3.Error as e: app.logger.error(f"DB error fetching videos: {e}"); flash("Error loading videos.", "error")
    
    return render_template("videos.html", videos=videos_for_template, camera_id=camera_id, username=session['username'], description=camera.description, current_sort_by=sort_by, current_sort_order=sort_order)

@app.route('/incident_videos/<camera_id>')
@login_required
def incident_videos(camera_id):
    camera = cameras.get(camera_id)
    if camera is None: flash(f"Camera {camera_id} not found.", "error"); return redirect(url_for('camera_list'))

    sort_by = request.args.get('sort_by', 'timestamp')
    sort_order = request.args.get('sort_order', 'desc')
    db_col = ALLOWED_SORT_COLUMNS_INCIDENTS.get(sort_by, 'incident_trigger_timestamp')
    sql_order = 'DESC' if sort_order == 'desc' else 'ASC'
    order_by_clause = f"{db_col} {sql_order}"
    if db_col == 'incident_trigger_timestamp': order_by_clause += f", original_video_filename {sql_order}"
    else: order_by_clause += f", id {sql_order}"

    conn = get_db(); cursor = conn.cursor()
    query = f"SELECT id, original_video_filename, incident_folder_name, incident_trigger_timestamp, path FROM {INCIDENT_TABLE_NAME} WHERE camera_id = ? ORDER BY {order_by_clause}"
    incidents_for_template = []
    try:
        cursor.execute(query, (str(camera_id),))
        for row in cursor.fetchall():
            incidents_for_template.append({
                "id": row["id"], "filename": row["original_video_filename"],
                "incident_folder": row["incident_folder_name"],
                "timestamp": row["incident_trigger_timestamp"], # Raw ISO
                "display_timestamp": format_display_timestamp_sgt(row["incident_trigger_timestamp"]),
                "path": row["path"]
            })
    except sqlite3.Error as e: app.logger.error(f"DB error fetching incidents: {e}"); flash("Error loading incidents.", "error")

    return render_template("incident_vid.html", incident_videos=incidents_for_template, camera_id=camera_id, username=session['username'], description=camera.description, current_sort_by=sort_by, current_sort_order=sort_order)


@app.route('/settings_page/<camera_id>')
@login_required
def settings_page(camera_id):
    camera = cameras.get(camera_id)
    if camera is None: return "Camera not found", 404
    current_settings = camera.settings # Already loaded
    return render_template('settings.html', camera_id=camera_id, username=session['username'], current_settings=current_settings, description=camera.description)

@app.route('/update_settings/<camera_id>', methods=['POST'])
@login_required
def update_settings(camera_id):
    camera = cameras.get(camera_id)
    if camera is None: flash(f"Camera {camera_id} not found.", 'error'); return redirect(url_for("camera_list"))
    max_videos_str = request.form.get("max_videos")
    video_duration_str = request.form.get("video_duration")
    error = None; validated_max_videos = None; validated_video_duration = None
    try:
        validated_max_videos = int(max_videos_str)
        validated_video_duration = int(video_duration_str)
        if validated_max_videos <= 0 or validated_video_duration <= 0:
            error = "Values must be positive."
    except ValueError: error = "Values must be whole numbers."
    
    if error:
        flash(error, 'error')
        return render_template('settings.html', camera_id=camera_id, current_settings=camera.settings, description=camera.description), 400
    
    if camera.update_settings(validated_max_videos, validated_video_duration):
        flash(f"Settings for Camera {camera_id} updated.", 'success')
    else:
        flash(f"Failed to save settings for Camera {camera_id}.", 'error')
    return redirect(url_for("index", camera_id=camera_id))


@app.route("/update_info/<camera_id>", methods=["POST"]) # No actual update, just redirect
def update_info(camera_id): return redirect(url_for("videos", camera_id=camera_id))

@app.route("/update_incident_info/<camera_id>", methods=["POST"]) # No actual update, just redirect
def update_incident_info(camera_id): return redirect(url_for("incident_videos", camera_id=camera_id))

@app.route('/start_recording/<camera_id>', methods=['POST'])
@login_required
def start_recording(camera_id):
    camera = cameras.get(camera_id)
    if camera is None: return "Camera not found", 404
    if camera.start_recording_thread():
        session['recording_status'] = 'recording' # Store in session for immediate UI update
        return "Recording started", 200
    return "Failed to start recording", 500

@app.route('/stop_recording/<camera_id>', methods=['POST'])
@login_required
def stop_recording(camera_id):
    camera = cameras.get(camera_id)
    if camera is None: return "Camera not found", 404
    camera.stop_recording_logic()
    session['recording_status'] = 'stopped' # Store in session
    return "Recording stopped", 200

@app.route('/simulate_incident/<camera_id>', methods=['POST'])
@login_required
def simulate_incident(camera_id):
    camera = cameras.get(camera_id)
    if camera is None: return "Camera not found", 404
    camera.simulate_incident()
    return '', 204

@app.route("/video_feed/<camera_id>")
def video_feed(camera_id):
    camera = cameras.get(camera_id)
    if camera is None: return "Camera not found", 404
    return Response(camera.generate_video_feed(), mimetype="multipart/x-mixed-replace; boundary=frame")

# --- DB-based Video Serving and Deletion ---
@app.route('/serve_main_video/<int:video_db_id>')
@login_required
def serve_main_recorded_video(video_db_id):
    db = get_db(); cursor = db.cursor()
    cursor.execute(f"SELECT path FROM {TABLE_NAME} WHERE id = ?", (video_db_id,))
    video_data = cursor.fetchone()
    if not video_data: return "Video not found in DB", 404
    
    dir_to_serve = os.path.join(PROJECT_ROOT_DIR, os.path.dirname(video_data["path"]))
    filename_on_disk = os.path.basename(video_data["path"])
    try:
        return send_from_directory(dir_to_serve, filename_on_disk, as_attachment=False)
    except FileNotFoundError: return "Video file not found on server", 404
    except Exception as e: app.logger.error(f"Serve error: {e}"); return "Error serving video", 500

@app.route('/serve_main_incident_video/<int:incident_db_id>')
@login_required
def serve_main_incident_video(incident_db_id):
    db = get_db(); cursor = db.cursor()
    cursor.execute(f"SELECT path FROM {INCIDENT_TABLE_NAME} WHERE id = ?", (incident_db_id,))
    incident_data = cursor.fetchone()
    if not incident_data: return "Incident video not found in DB", 404
    
    dir_to_serve = os.path.join(PROJECT_ROOT_DIR, os.path.dirname(incident_data["path"]))
    filename_on_disk = os.path.basename(incident_data["path"])
    try:
        return send_from_directory(dir_to_serve, filename_on_disk, as_attachment=False)
    except FileNotFoundError: return "Incident video file not found on server", 404
    except Exception as e: app.logger.error(f"Serve incident error: {e}"); return "Error serving incident video", 500

@app.route('/delete_main_video/<int:video_db_id>', methods=['DELETE'])
@login_required
def delete_main_recorded_video(video_db_id):
    db = get_db(); cursor = db.cursor()
    cursor.execute(f"SELECT path FROM {TABLE_NAME} WHERE id = ?", (video_db_id,))
    video_entry = cursor.fetchone()
    if not video_entry: return jsonify({"success": False, "error": "Video not found in DB"}), 404
    
    abs_file_path = os.path.join(PROJECT_ROOT_DIR, video_entry["path"])
    try:
        if os.path.exists(abs_file_path): os.remove(abs_file_path)
        cursor.execute(f"DELETE FROM {TABLE_NAME} WHERE id = ?", (video_db_id,))
        db.commit()
        return jsonify({"success": True, "message": "Video deleted."}), 200
    except Exception as e:
        db.rollback(); app.logger.error(f"Delete video error: {e}")
        return jsonify({"success": False, "error": "Server error during deletion"}), 500

@app.route('/delete_main_incident_video/<int:incident_db_id>', methods=['DELETE'])
@login_required
def delete_main_incident_video(incident_db_id):
    db = get_db(); cursor = db.cursor()
    cursor.execute(f"SELECT path FROM {INCIDENT_TABLE_NAME} WHERE id = ?", (incident_db_id,))
    incident_entry = cursor.fetchone()
    if not incident_entry: return jsonify({"success": False, "error": "Incident video not found in DB"}), 404

    abs_file_path = os.path.join(PROJECT_ROOT_DIR, incident_entry["path"])
    try:
        if os.path.exists(abs_file_path): os.remove(abs_file_path)
        cursor.execute(f"DELETE FROM {INCIDENT_TABLE_NAME} WHERE id = ?", (incident_db_id,))
        db.commit()
        return jsonify({"success": True, "message": "Incident video deleted."}), 200
    except Exception as e:
        db.rollback(); app.logger.error(f"Delete incident error: {e}")
        return jsonify({"success": False, "error": "Server error during deletion"}), 500

# --- Sensor Info Page Route ---
@app.route('/sensor_info/<camera_id>', methods=['GET', 'POST'])
@login_required
def sensor_info_page(camera_id):
    camera = cameras.get(camera_id)
    if not camera: flash(f"Camera {camera_id} not found.", "error"); return redirect(url_for('camera_list'))

    all_sensor_data = load_sensor_data()
    current_sensor_details = all_sensor_data.get(str(camera_id), {})

    if request.method == 'POST':
        sensor_type = request.form.get('sensor_type', '').strip()
        sensor_location = request.form.get('sensor_location', '').strip()
        sensor_notes = request.form.get('sensor_notes', '').strip()
        if not sensor_type and not sensor_location and not sensor_notes:
            flash("Please provide some sensor information.", "warning")
        else:
            all_sensor_data[str(camera_id)] = {"type": sensor_type, "location": sensor_location, "notes": sensor_notes}
            if save_sensor_data(all_sensor_data):
                flash(f"Sensor info for Camera {camera.description} updated.", "success")
                current_sensor_details = all_sensor_data[str(camera_id)] # Update for display
            else:
                flash(f"Failed to save sensor info for Camera {camera.description}.", "error")
        return redirect(url_for('sensor_info_page', camera_id=camera_id)) # Redirect after POST

    incident_timestamps_list = []; total_recordings = 0; total_incident_clips = 0
    conn = get_db(); cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT COUNT(id) FROM {TABLE_NAME} WHERE camera_id = ?", (str(camera_id),))
        res = cursor.fetchone(); total_recordings = res[0] if res else 0
        cursor.execute(f"SELECT COUNT(id) FROM {INCIDENT_TABLE_NAME} WHERE camera_id = ?", (str(camera_id),))
        res = cursor.fetchone(); total_incident_clips = res[0] if res else 0
        cursor.execute(f"SELECT DISTINCT incident_trigger_timestamp FROM {INCIDENT_TABLE_NAME} WHERE camera_id = ? ORDER BY incident_trigger_timestamp DESC", (str(camera_id),))
        for row in cursor.fetchall():
            incident_timestamps_list.append({"raw_iso": row["incident_trigger_timestamp"], "display_sgt": format_display_timestamp_sgt(row["incident_trigger_timestamp"])})
    except sqlite3.Error as e: app.logger.error(f"DB error on sensor page: {e}"); flash("Error loading data.", "error")

    return render_template('sensor_info.html', username=session['username'], camera_id=camera_id, camera_description=camera.description, sensor_details=current_sensor_details, incident_timestamps=incident_timestamps_list, total_recordings=total_recordings, total_incident_clips=total_incident_clips)


def release_all_cameras():
    app.logger.info("Releasing all camera captures on exit...")
    global cameras
    if cameras:
        for cam_id, cam_obj in cameras.items():
             if isinstance(cam_obj, Camera):
                 cam_obj.release_capture()
        app.logger.info("Camera release attempts finished.")
    else:
        app.logger.info("No camera objects found to release.")
atexit.register(release_all_cameras)


if __name__ == "__main__":
    # load_cameras_from_json() # Already called globally
    
    def delayed_auto_start():
        time.sleep(2) # Give Flask a moment to set up
        # Access auto_record_camera which should be set by load_cameras_from_json
        global auto_record_camera
        if auto_record_camera:
            app.logger.info(f"[Delayed Auto-Start] For camera: {auto_record_camera.camera_id}")
            if not auto_record_camera.recording:
                auto_record_camera.start_recording_thread()
            else:
                app.logger.info(f"Camera {auto_record_camera.camera_id} already recording.")
        else:
            app.logger.warning(f"Auto-record camera ID '{auto_record_camera_id}' not found or default creation failed. Auto-recording NOT started.")

    auto_start_thread = Thread(target=delayed_auto_start)
    auto_start_thread.daemon = True
    auto_start_thread.start()
    
    host = '0.0.0.0'
    app.run(debug=True, host=host, port=5001, use_reloader=False) # use_reloader=False is important for threads and atexit