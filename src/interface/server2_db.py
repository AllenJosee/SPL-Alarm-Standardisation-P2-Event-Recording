#add the video metadata to sqlite3 database
#retrieve and playback?

from flask import Flask, render_template, Response, request, redirect, url_for, jsonify, session, send_from_directory, flash, g
import os
import cv2
import json
from threading import Thread
import time
import shutil
from functools import wraps
import atexit
import sqlite3
import logging

logging.basicConfig(level=logging.INFO)

# Initialize Flask app
app = Flask(__name__, template_folder='static/templates')
app.secret_key = '14a6a86bf47bf75c4479c0c70886b2a5'

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# SRC_DIR should be SPL-Alram-P2-Database/src/
SRC_DIR = os.path.dirname(SCRIPT_DIR)
# PROJECT_ROOT_DIR should be SPL-Alram-P2-Database/
PROJECT_ROOT_DIR = os.path.dirname(SRC_DIR)

DATABASE_NAME = 'videos.db'
TABLE_NAME = 'video_metadata'
INCIDENT_TABLE_NAME = 'incident_video_metadata'

# DATABASE_PATH will be SPL-Alram-P2-Database/videos.db
DATABASE_PATH = os.path.join(PROJECT_ROOT_DIR, DATABASE_NAME)
app.logger.setLevel(logging.INFO)

'''recordings_dir = "src/recordings"
incidents_dir = "src/incidents"
settings_file = "src/settings.json"'''


# --- Flask Database Helper Functions ---
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE_PATH)
        db.row_factory = sqlite3.Row # Access columns by name
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    conn = sqlite3.connect(DATABASE_PATH) # Use the constant
    cursor = conn.cursor()
    
    # Create table for normal recordings (if not exists)
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE 
        )
    ''') # Added UNIQUE constraint to path for regular recordings

    # Create table for incident videos (if not exists)
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS {INCIDENT_TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            incident_trigger_timestamp TEXT NOT NULL, -- Timestamp when the incident was triggered/created
            original_video_filename TEXT NOT NULL, -- The filename of the video that was copied
            original_video_timestamp TEXT, -- Optional: original timestamp of the copied video
            incident_folder_name TEXT NOT NULL, -- Name of the folder like camX_incident_YYYYMMDD-HHMMSS
            path TEXT NOT NULL UNIQUE -- Full relative path to the copied incident video, e.g., src/incidents/cameraX/incident_folder_name/video.mp4
        )
    ''') # Added UNIQUE constraint to path for incident recordings
    conn.commit()
    conn.close()
    app.logger.info(f"Database {DATABASE_NAME} initialized with tables: {TABLE_NAME}, {INCIDENT_TABLE_NAME}")

init_db() 


def get_latest_recordings(limit=100):
    conn = sqlite3.connect('videos.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM video_metadata ORDER BY timestamp DESC LIMIT ?', (limit,))
    latest_recordings = cursor.fetchall()
    conn.close()
    return latest_recordings


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

    def load_videos_from_database(self):
        """Loads video metadata from the database for this specific camera."""
        conn = None
        videos = []
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            query = f"SELECT id, filename, timestamp, path FROM {TABLE_NAME} WHERE camera_id = ? ORDER BY timestamp DESC"
            cursor.execute(query, (str(self.camera_id),)) # Ensure camera_id matches type
            db_videos = cursor.fetchall()

            for row in db_videos:
                # Construct absolute path to check existence and get ctime for display
                absolute_file_path = os.path.join(PROJECT_ROOT_DIR, row["path"])
                file_exists = os.path.exists(absolute_file_path)
                
                raw_ts_from_file = 0
                display_ts_from_file = "N/A (File Missing)"
                if file_exists:
                    try:
                        raw_ts_from_file = os.path.getctime(absolute_file_path)
                        display_ts_from_file = time.ctime(raw_ts_from_file)
                    except Exception as e:
                        app.logger.warning(f"Error getting ctime for {absolute_file_path}: {e}")
                
                videos.append({
                    "id": row["id"],
                    "filename": row["filename"],
                    "timestamp": row["timestamp"], # DB timestamp (YYYYMMDD-HHMMSS)
                    "display_timestamp": display_ts_from_file, # User-friendly from file ctime
                    "raw_timestamp": raw_ts_from_file, # Actual file ctime for sorting if needed
                    "path": row["path"] # Relative path from DB
                })
            app.logger.info(f"[Camera {self.camera_id}] Loaded {len(videos)} videos from database.")
        except sqlite3.Error as e:
            app.logger.error(f"[Camera {self.camera_id}] Database error loading videos: {e}")
        except Exception as e:
            app.logger.error(f"[Camera {self.camera_id}] General error loading videos from database: {e}")
        finally:
            if conn:
                conn.close()
        return videos


    def load_incident_videos_from_database(self):
        """Loads incident video metadata from the database for this camera."""
        conn = None
        incident_videos = []
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # Select relevant fields for display
            query = f"""
                SELECT id, incident_trigger_timestamp, original_video_filename, incident_folder_name, path 
                FROM {INCIDENT_TABLE_NAME} 
                WHERE camera_id = ? 
                ORDER BY incident_trigger_timestamp DESC, id DESC
            """ # Sort by incident creation time, then by ID if same time
            cursor.execute(query, (str(self.camera_id),))
            db_incidents = cursor.fetchall()

            for row in db_incidents:
                absolute_file_path = os.path.join(PROJECT_ROOT_DIR, row["path"])
                file_exists = os.path.exists(absolute_file_path)
                
                # We use incident_trigger_timestamp for display primarily
                display_ts = row["incident_trigger_timestamp"] # Can be formatted if needed
                if not file_exists:
                    display_ts += " (File Missing)"

                incident_videos.append({
                    "id": row["id"], # DB ID of the incident video entry
                    "filename": row["original_video_filename"], # The name of the video file
                    "incident_folder": row["incident_folder_name"],
                    "timestamp": row["incident_trigger_timestamp"], # Main timestamp for this entry
                    "display_timestamp": display_ts, # For direct display
                    "path": row["path"] # Relative path for serving/deletion
                })
            app.logger.info(f"[Camera {self.camera_id}] Loaded {len(incident_videos)} incidents from database.")
        except sqlite3.Error as e:
            app.logger.error(f"[Camera {self.camera_id}] Database error loading incidents: {e}")
        except Exception as e:
            app.logger.error(f"[Camera {self.camera_id}] General error loading incidents from database: {e}")
        finally:
            if conn: conn.close()
        return incident_videos

    
    def update_settings(self, max_videos, video_duration):
        self.settings["max_videos"] = max_videos
        self.settings["video_duration"] = video_duration
        with open(self.settings_file, "w") as f:
            json.dump(self.settings, f)

    def record_video(self):
        capture = self._get_or_init_capture()
        if not capture:
            app.logger.error(f"Cam {self.camera_id}: No capture device.")
            self.recording = False
            return

        while self.recording:
            # Timestamp for filename and DB
            formatted_timestamp = time.strftime("%Y%m%d-%H%M%S")
            filename = f"cam{str(self.camera_id)}_{formatted_timestamp}.mp4"

            # self.recordings_dir is like "src/recordings/camera1" (relative to PROJECT_ROOT)
            # This path is what gets stored in the database
            relative_path_for_db = os.path.join(self.recordings_dir, filename)
            # This is the full path for OpenCV to write the file
            absolute_filepath_for_cv = os.path.join(PROJECT_ROOT_DIR, relative_path_for_db)

            # Ensure the directory exists for OpenCV to write into
            os.makedirs(os.path.dirname(absolute_filepath_for_cv), exist_ok=True)

            fourcc = cv2.VideoWriter_fourcc(*"avc1") # Or "mp4v"
            
            # Load settings if not already loaded (for video_duration)
            if not hasattr(self, 'settings') or not self.settings:
                self.load_settings()
            
            video_duration_seconds = self.settings.get("video_duration", 5) # Default 5s
            fps = 15 # Or from settings

            try:
                if not capture.isOpened():
                    app.logger.error(f"Cam {self.camera_id}: Capture lost before VideoWriter.")
                    self.recording = False; break
                
                frame_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
                frame_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))

                app.logger.info(f"Cam {self.camera_id}: Writing to {absolute_filepath_for_cv} ({frame_width}x{frame_height}@{fps}fps)")
                out = cv2.VideoWriter(absolute_filepath_for_cv, fourcc, fps, (frame_width, frame_height))
                if not out.isOpened():
                    app.logger.error(f"Cam {self.camera_id}: Failed to open VideoWriter for {absolute_filepath_for_cv}"); time.sleep(1); continue
            except Exception as e:
                app.logger.error(f"Cam {self.camera_id}: Error creating VideoWriter: {e}"); time.sleep(1); continue

            start_time = time.time()
            frame_count = 0
            total_frames_to_record = int(fps * video_duration_seconds)

            while self.recording and frame_count < total_frames_to_record:
                if not capture.isOpened():
                    app.logger.error(f"Cam {self.camera_id}: Capture lost during recording."); self.recording = False; break
                ret, frame = capture.read()
                if ret:
                    out.write(frame); frame_count += 1
                else:
                    app.logger.warning(f"Cam {self.camera_id}: Failed to read frame."); time.sleep(0.05)
            out.release()
            app.logger.info(f"Cam {self.camera_id}: Wrote {frame_count} frames to {filename}.")

            if frame_count > 0: # Only insert and prune if video was actually created
                # Store metadata in DB. Pass the *relative_path_for_db*
                self.insert_video_metadata(filename, formatted_timestamp, relative_path_for_db)
                self.prune_videos_from_database() # New DB-based pruning
            else:
                app.logger.warning(f"Cam {self.camera_id}: No frames recorded for {filename}. Not saving to DB or pruning.")
                # Optionally delete the empty/failed file
                if os.path.exists(absolute_filepath_for_cv):
                    try:
                        os.remove(absolute_filepath_for_cv)
                        app.logger.info(f"Cam {self.camera_id}: Removed empty/failed file {absolute_filepath_for_cv}")
                    except OSError as e:
                        app.logger.error(f"Cam {self.camera_id}: Error removing empty/failed file {absolute_filepath_for_cv}: {e}")

    def prune_videos_from_database(self):
        """Prunes oldest videos for this camera based on settings, using the database."""
        conn = None
        try:
            if not hasattr(self, 'settings') or not self.settings: self.load_settings()
            max_videos = self.settings.get("max_videos", 5)

            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query_select = f"SELECT id, path FROM {TABLE_NAME} WHERE camera_id = ? ORDER BY timestamp ASC" # Oldest first
            cursor.execute(query_select, (str(self.camera_id),))
            all_camera_videos_in_db = cursor.fetchall()

            num_videos_in_db = len(all_camera_videos_in_db)
            app.logger.info(f"[PruneDB Cam {self.camera_id}] Found {num_videos_in_db} videos. Max: {max_videos}")

            if num_videos_in_db > max_videos:
                num_to_delete = num_videos_in_db - max_videos
                videos_to_delete = all_camera_videos_in_db[:num_to_delete]
                app.logger.info(f"[PruneDB Cam {self.camera_id}] Deleting {len(videos_to_delete)} oldest video(s).")

                for video_entry in videos_to_delete:
                    db_id_to_delete = video_entry["id"]
                    relative_path = video_entry["path"]
                    absolute_file_path_to_delete = os.path.join(PROJECT_ROOT_DIR, relative_path)

                    if os.path.exists(absolute_file_path_to_delete):
                        try:
                            os.remove(absolute_file_path_to_delete)
                            app.logger.info(f"[PruneDB Cam {self.camera_id}] Deleted file: {absolute_file_path_to_delete}")
                        except OSError as e:
                            app.logger.error(f"[PruneDB Cam {self.camera_id}] Error deleting file {absolute_file_path_to_delete}: {e}")
                    else:
                        app.logger.warning(f"[PruneDB Cam {self.camera_id}] File {absolute_file_path_to_delete} not found for (DB ID: {db_id_to_delete})")
                    
                    query_delete_db = f"DELETE FROM {TABLE_NAME} WHERE id = ?"
                    cursor.execute(query_delete_db, (db_id_to_delete,))
                conn.commit()
                app.logger.info(f"[PruneDB Cam {self.camera_id}] DB entries deleted.")
        except sqlite3.Error as e:
            if conn: conn.rollback()
            app.logger.error(f"[PruneDB Cam {self.camera_id}] SQLite Error: {e}")
        except Exception as e:
            app.logger.error(f"[PruneDB Cam {self.camera_id}] General Error: {e}")
        finally:
            if conn: conn.close()

        
    def insert_video_metadata(self, filename, timestamp_str, relative_path_to_project_root):
        """Inserts video metadata into the database.
        'relative_path_to_project_root' is the path like 'src/recordings/camera1/file.mp4'
        """
        conn = None
        try:
            # It's generally safer to use the DATABASE_PATH constant here
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            query = f"""
                INSERT INTO {TABLE_NAME} (camera_id, filename, timestamp, path)
                VALUES (?, ?, ?, ?)
            """
            # Ensure self.camera_id is a string if your DB column is TEXT
            cursor.execute(query, (str(self.camera_id), filename, timestamp_str, relative_path_to_project_root))
            conn.commit()
            app.logger.debug(f"DB Insert for Camera {self.camera_id}: File={filename}, Path={relative_path_to_project_root}")
        except sqlite3.Error as e:
            app.logger.error(f"DB Error for Camera {self.camera_id} inserting {filename}: {e}")
            if conn:
                conn.rollback() # Rollback on error
        finally:
            if conn:
                conn.close()

    def insert_incident_video_metadata(self, incident_trigger_ts, original_filename, original_ts, incident_folder, copied_video_relative_path):
        conn = None
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            query = f"""
                INSERT INTO {INCIDENT_TABLE_NAME} 
                (camera_id, incident_trigger_timestamp, original_video_filename, original_video_timestamp, incident_folder_name, path)
                VALUES (?, ?, ?, ?, ?, ?)
            """
            cursor.execute(query, (
                str(self.camera_id),
                incident_trigger_ts,
                original_filename,
                original_ts, # Can be None if not available
                incident_folder,
                copied_video_relative_path
            ))
            conn.commit()
            app.logger.debug(f"DB Incident Insert: CamID={self.camera_id}, File={original_filename}, Path={copied_video_relative_path}")
        except sqlite3.Error as e:
            app.logger.error(f"DB Error Camera {self.camera_id} inserting incident {original_filename}: {e}")
            if conn: conn.rollback()
        finally:
            if conn: conn.close()

    def get_videos_by_camera(camera_id):
        conn = sqlite3.connect('videos.db')
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM video_metadata WHERE camera_id = ?', (camera_id,))
        videos = cursor.fetchall()
        conn.close()
        return videos

    
    def simulate_incident(self):
        incident_trigger_timestamp = time.strftime("%Y%m%d-%H%M%S")
        incident_folder_name_only = f"cam{str(self.camera_id)}_incident_{incident_trigger_timestamp}"
        
        # self.incidents_dir is like "src/incidents/camera1"
        # relative_incident_folder_path is "src/incidents/camera1/cam1_incident_XYZ"
        relative_incident_folder_path = os.path.join(self.incidents_dir, incident_folder_name_only)
        # absolute_incident_folder_path is the full disk path for os.makedirs
        absolute_incident_folder_path = os.path.join(PROJECT_ROOT_DIR, relative_incident_folder_path)
        
        os.makedirs(absolute_incident_folder_path, exist_ok=True)
        app.logger.info(f"Camera {self.camera_id}: Created incident folder {absolute_incident_folder_path}")

        videos_from_db = self.load_videos_from_database() # Gets normal recordings, newest first
        
        copied_count = 0
        for video_data in videos_from_db:
            if copied_count >= 6: break # Max 6 videos

            original_relative_path = video_data["path"] # e.g., "src/recordings/camera1/video.mp4"
            original_filename = video_data["filename"]
            original_timestamp_from_db = video_data["timestamp"] # YYYYMMDD-HHMMSS format

            source_absolute_path = os.path.join(PROJECT_ROOT_DIR, original_relative_path)
            
            # Destination path for the copied file
            # relative_copied_video_path: "src/incidents/camera1/incident_folder_name/original_filename.mp4"
            relative_copied_video_path = os.path.join(relative_incident_folder_path, original_filename)
            destination_absolute_path = os.path.join(PROJECT_ROOT_DIR, relative_copied_video_path)

            if os.path.exists(source_absolute_path):
                try:
                    shutil.copy(source_absolute_path, destination_absolute_path)
                    app.logger.info(f"Incident Cam {self.camera_id}: Copied {original_filename} to {incident_folder_name_only}")
                    
                    # Insert metadata for the copied incident video
                    self.insert_incident_video_metadata(
                        incident_trigger_timestamp,
                        original_filename,
                        original_timestamp_from_db, # Store original video's timestamp
                        incident_folder_name_only,  # Just the folder name part
                        relative_copied_video_path  # Full relative path to the copied file
                    )
                    copied_count += 1
                except Exception as e:
                    app.logger.error(f"Incident Cam {self.camera_id}: Error copying/logging {original_filename}: {e}")
            else:
                app.logger.warning(f"Incident Cam {self.camera_id}: Source file {source_absolute_path} missing for copy.")

    #Video Feed
    def generate_video_feed(self):
        capture = self._get_or_init_capture() # Get/init camera first
        if not capture:
            print(f"Cannot generate feed for camera {self.camera_id}: Capture device not available.")
            return
        while True:
            if not capture.isOpened(): # Check if capture is still valid
                 print(f"Error: Camera {self.camera_id} capture lost during feed generation.")
                 break # Exit the loop if camera fails

            ret, frame = capture.read() # Use the initialized capture object
            if not ret:
                print(f"Warning: Could not read frame from camera {self.camera_id} for feed.")
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


### Flask Routes ###
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

        # Get paths before deleting the camera object from the 'cameras' dictionary
        recordings_path_to_remove = camera_to_delete.recordings_dir # This is like "src/recordings/cameraX"
        incidents_path_to_remove = camera_to_delete.incidents_dir
        settings_path_to_remove = camera_to_delete.settings_file
        
        # Store the camera_id for DB deletion *before* 'camera_to_delete' might become invalid
        camera_id_for_db = str(camera_to_delete.camera_id) # Ensure it's a string if DB expects TEXT

        try: 
            # 1. Delete from the 'cameras' in-memory dictionary and JSON file
            del cameras[camera_id]
            save_cameras_to_json()
            app.logger.info(f"Camera object for ID {camera_id_for_db} removed from memory and cameras.json.")

            # 2. Delete physical folders and files
            # Note: recordings_path_to_remove is relative to PROJECT_ROOT_DIR
            # We need the absolute path for shutil.rmtree
            absolute_recordings_path = os.path.join(PROJECT_ROOT_DIR, recordings_path_to_remove)
            if os.path.exists(absolute_recordings_path): # Check absolute path
                shutil.rmtree(absolute_recordings_path)
                app.logger.info(f"Successfully deleted recordings directory: {absolute_recordings_path}")
            else:
                app.logger.warning(f"Recordings directory not found, skipping deletion: {absolute_recordings_path}")

            absolute_incidents_path = os.path.join(PROJECT_ROOT_DIR, incidents_path_to_remove)
            if os.path.exists(absolute_incidents_path): # Check absolute path
                shutil.rmtree(absolute_incidents_path)
                app.logger.info(f"Successfully deleted incidents directory: {absolute_incidents_path}")
            else:
                app.logger.warning(f"Incidents directory not found, skipping deletion: {absolute_incidents_path}")

            # Settings file is likely relative to where the app runs (src/interface/) or PROJECT_ROOT_DIR
            # If settings_file is stored like "src/settings_camera1.json", then join with PROJECT_ROOT_DIR
            absolute_settings_path = os.path.join(PROJECT_ROOT_DIR, settings_path_to_remove)
            if settings_path_to_remove and os.path.exists(absolute_settings_path):
                os.remove(absolute_settings_path)
                app.logger.info(f"Successfully deleted settings file: {absolute_settings_path}")
            else:
                app.logger.warning(f"Settings file not found or path invalid, skipping deletion: {absolute_settings_path}")

            # 3. <<< NEW: Delete corresponding video metadata from the database >>>
            conn = None
            try:
                conn = sqlite3.connect(DATABASE_PATH)
                cursor = conn.cursor()
                
                # Delete from normal video_metadata
                query_delete_recordings = f"DELETE FROM {TABLE_NAME} WHERE camera_id = ?"
                cursor.execute(query_delete_recordings, (camera_id_for_db,))
                deleted_recordings_count = cursor.rowcount
                app.logger.info(f"Deleted {deleted_recordings_count} entries from {TABLE_NAME} for cam_id: {camera_id_for_db}")

                # <<< NEW: Delete from incident_video_metadata >>>
                query_delete_incidents = f"DELETE FROM {INCIDENT_TABLE_NAME} WHERE camera_id = ?"
                cursor.execute(query_delete_incidents, (camera_id_for_db,))
                deleted_incidents_count = cursor.rowcount
                app.logger.info(f"Deleted {deleted_incidents_count} entries from {INCIDENT_TABLE_NAME} for cam_id: {camera_id_for_db}")
                
                conn.commit()
            except sqlite3.Error as e:
                if conn: conn.rollback()
                app.logger.error(f"DB error deleting metadata for cam_id {camera_id_for_db}: {e}")
                flash(f"Error deleting records from database for camera {camera_id_for_db}. Check logs.", "error")
            finally:
                if conn: conn.close()
                
            flash(f"Camera {camera_id_for_db} and all associated data have been deleted.", "success")
            return redirect(url_for('camera_list'))

        except OSError as e:
            app.logger.error(f"OS Error deleting files/folders for camera {camera_id_for_db}: {e}")
            flash(f"Error deleting files/folders for camera {camera_id_for_db}.", "error")
            return redirect(url_for('camera_list')) # Or an error page
        except Exception as e:
            app.logger.error(f"Unexpected error deleting camera {camera_id_for_db}: {e}")
            flash(f"An unexpected error occurred while deleting camera {camera_id_for_db}.", "error")
            return redirect(url_for('camera_list'))
        
    flash(f"Camera {camera_id} not found.", "warning")
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
        flash(f"Camera {camera_id} not found.", "error")
        return redirect(url_for('camera_list'))

    # Get sort parameters
    sort_by_param = request.args.get('sort_by', 'timestamp') # Default sort by DB timestamp
    sort_order_param = request.args.get('sort_order', 'desc') # Default descending

    allowed_sort_columns = {'id': 'id', 'camera_id': 'camera_id', 'filename': 'filename', 'timestamp': 'timestamp'}
    db_column_to_sort = allowed_sort_columns.get(sort_by_param, 'timestamp')
    sql_sort_order = 'DESC' if sort_order_param.lower() == 'desc' else 'ASC'

    # Fetch videos from database for this camera with sorting
    conn = get_db() # Use Flask's g-managed connection
    cursor = conn.cursor()
    query = f"""
        SELECT id, filename, timestamp, path 
        FROM {TABLE_NAME} 
        WHERE camera_id = ? 
        ORDER BY {db_column_to_sort} {sql_sort_order}
    """
    try:
        cursor.execute(query, (str(camera_id),))
        videos_from_db = cursor.fetchall() # List of Row objects
        
        # Prepare data for template (similar to Camera.load_videos_from_database logic for display_timestamp)
        videos_for_template = []
        for row in videos_from_db:
            absolute_file_path = os.path.join(PROJECT_ROOT_DIR, row["path"])
            file_exists = os.path.exists(absolute_file_path)
            raw_ts_from_file = os.path.getctime(absolute_file_path) if file_exists else 0
            display_ts_from_file = time.ctime(raw_ts_from_file) if file_exists else "N/A (File Missing)"
            
            videos_for_template.append({
                "id": row["id"],
                "filename": row["filename"],
                "timestamp": row["timestamp"], # DB timestamp
                "display_timestamp": display_ts_from_file,
                "raw_timestamp": raw_ts_from_file # For client-side sort if any, or just info
            })

    except sqlite3.Error as e:
        app.logger.error(f"DB error fetching videos for cam {camera_id}: {e}")
        videos_for_template = []
        flash("Error loading videos from database.", "error")

    app.logger.debug(f"Route /videos/{camera_id}: Displaying {len(videos_for_template)} videos.")
    return render_template("videos.html", 
                           videos=videos_for_template, 
                           camera_id=camera_id, 
                           username=session['username'], 
                           description=camera.description,
                           current_sort_by=sort_by_param,
                           current_sort_order=sort_order_param)

@app.route('/incident_videos/<camera_id>')
@login_required
def incident_videos(camera_id): # Route function name is fine
    camera = cameras.get(camera_id)
    if camera is None:
        flash(f"Camera {camera_id} not found.", "error")
        return redirect(url_for('camera_list'))

    # Fetch incident videos from the database using the Camera class method
    incident_videos_list = camera.load_incident_videos_from_database()
    
    # The list from load_incident_videos_from_database is already sorted.
    # If you want to add URL-based sorting like for normal videos, you'd implement
    # similar logic here, querying INCIDENT_TABLE_NAME directly with get_db().
    
    app.logger.debug(f"Route /incident_videos/{camera_id}: Displaying {len(incident_videos_list)} incidents from DB.")
    return render_template("incident_vid.html", 
                           incident_videos=incident_videos_list, 
                           camera_id=camera_id, 
                           username=session['username'], 
                           description=camera.description)

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
@app.route('/serve_main_video/<int:video_db_id>')
@login_required
def serve_main_recorded_video(video_db_id):
    db = get_db()
    cursor = db.cursor()
    query = f"SELECT path, filename FROM {TABLE_NAME} WHERE id = ?"
    cursor.execute(query, (video_db_id,))
    video_data = cursor.fetchone()

    if not video_data:
        app.logger.error(f"Serve: Video ID {video_db_id} not in DB.")
        return "Video not found in database", 404
    
    relative_path_from_db = video_data["path"]
    # Serve from PROJECT_ROOT_DIR based on the relative path from DB
    # send_from_directory needs the directory part and the filename part separately
    directory_to_serve_from = os.path.join(PROJECT_ROOT_DIR, os.path.dirname(relative_path_from_db))
    filename_on_disk = os.path.basename(relative_path_from_db)
    
    app.logger.info(f"Serve: ID={video_db_id}, Dir='{directory_to_serve_from}', File='{filename_on_disk}'")
    try:
        return send_from_directory(directory_to_serve_from, filename_on_disk, as_attachment=False)
    except FileNotFoundError:
        app.logger.error(f"Serve: File not on disk: {os.path.join(directory_to_serve_from, filename_on_disk)}")
        return "Video file not found on server", 404
    except Exception as e:
        app.logger.error(f"Serve: Error for ID {video_db_id}: {e}")
        return "Error serving video", 500
        return "Error serving video", 500

#New Route for incident playbacks
@app.route('/serve_main_incident_video/<int:incident_db_id>')
@login_required
def serve_main_incident_video(incident_db_id):
    db = get_db()
    cursor = db.cursor()
    query = f"SELECT path, original_video_filename FROM {INCIDENT_TABLE_NAME} WHERE id = ?"
    cursor.execute(query, (incident_db_id,))
    incident_data = cursor.fetchone()

    if not incident_data:
        app.logger.error(f"Serve Incident: ID {incident_db_id} not in DB ({INCIDENT_TABLE_NAME}).")
        return "Incident video not found in database", 404
    
    relative_path_from_db = incident_data["path"]
    directory_to_serve_from = os.path.join(PROJECT_ROOT_DIR, os.path.dirname(relative_path_from_db))
    filename_on_disk = os.path.basename(relative_path_from_db)
    
    app.logger.info(f"Serve Incident: ID={incident_db_id}, Dir='{directory_to_serve_from}', File='{filename_on_disk}'")
    try:
        # Use original_video_filename for download prompt if as_attachment=True
        return send_from_directory(directory_to_serve_from, filename_on_disk, as_attachment=False) 
    except FileNotFoundError:
        app.logger.error(f"Serve Incident: File not on disk: {os.path.join(directory_to_serve_from, filename_on_disk)}")
        return "Incident video file not found on server", 404
    except Exception as e:
        app.logger.error(f"Serve Incident: Error for ID {incident_db_id}: {e}")
        return "Error serving incident video", 500

#New route for video delete
@app.route('/delete_main_video/<int:video_db_id>', methods=['DELETE']) # Ensure method is DELETE
@login_required
def delete_main_recorded_video(video_db_id):
    db = get_db()
    cursor = db.cursor()
    
    query_select = f"SELECT path FROM {TABLE_NAME} WHERE id = ?"
    cursor.execute(query_select, (video_db_id,))
    video_entry = cursor.fetchone()

    if not video_entry:
        app.logger.error(f"Delete: Video ID {video_db_id} not in DB.")
        return jsonify({"success": False, "error": "Video not found in database"}), 404

    relative_path_from_db = video_entry["path"]
    absolute_file_path_on_disk = os.path.join(PROJECT_ROOT_DIR, relative_path_from_db)
    
    file_removed_from_disk = False
    try:
        if os.path.exists(absolute_file_path_on_disk):
            os.remove(absolute_file_path_on_disk)
            app.logger.info(f"Delete: File removed: {absolute_file_path_on_disk}")
            file_removed_from_disk = True
        else:
            app.logger.warning(f"Delete: File {absolute_file_path_on_disk} (DB ID: {video_db_id}) not on disk.")

        query_delete_db = f"DELETE FROM {TABLE_NAME} WHERE id = ?"
        cursor.execute(query_delete_db, (video_db_id,))
        db.commit()
        app.logger.info(f"Delete: DB entry ID {video_db_id} removed.")
        
        msg = "Video deleted."
        if file_removed_from_disk: msg += " File also removed."
        else: msg += " File was not found on disk."
        return jsonify({"success": True, "message": msg}), 200

    except sqlite3.Error as e:
        db.rollback()
        app.logger.error(f"Delete: SQLite error for ID {video_db_id}: {e}")
        return jsonify({"success": False, "error": "Database error during deletion"}), 500
    except OSError as e:
        # DB commit might have happened if os.remove failed after successful db delete query execution
        # but before commit for it. Consider order of operations or more complex transaction.
        app.logger.error(f"Delete: OS error deleting file {absolute_file_path_on_disk}: {e}")
        return jsonify({"success": False, "error": f"Server error deleting file: {e.strerror}"}), 500
    except Exception as e:
        db.rollback()
        app.logger.error(f"Delete: Unexpected error for ID {video_db_id}: {e}")
        return jsonify({"success": False, "error": "An unexpected server error occurred"}), 500

#New route for deleting incident videos
@app.route('/delete_main_incident_video/<int:incident_db_id>', methods=['DELETE'])
@login_required
def delete_main_incident_video(incident_db_id):
    db = get_db()
    cursor = db.cursor()
    
    query_select = f"SELECT path FROM {INCIDENT_TABLE_NAME} WHERE id = ?"
    cursor.execute(query_select, (incident_db_id,))
    incident_entry = cursor.fetchone()

    if not incident_entry:
        app.logger.error(f"Delete Incident: ID {incident_db_id} not in DB ({INCIDENT_TABLE_NAME}).")
        return jsonify({"success": False, "error": "Incident video not found in database"}), 404

    relative_path_from_db = incident_entry["path"]
    absolute_file_path_on_disk = os.path.join(PROJECT_ROOT_DIR, relative_path_from_db)
    
    file_removed = False
    try:
        if os.path.exists(absolute_file_path_on_disk):
            os.remove(absolute_file_path_on_disk)
            app.logger.info(f"Delete Incident: File removed: {absolute_file_path_on_disk}")
            file_removed = True
        else:
            app.logger.warning(f"Delete Incident: File {absolute_file_path_on_disk} (DB ID: {incident_db_id}) not on disk.")

        query_delete_db = f"DELETE FROM {INCIDENT_TABLE_NAME} WHERE id = ?"
        cursor.execute(query_delete_db, (incident_db_id,))
        db.commit()
        app.logger.info(f"Delete Incident: DB entry ID {incident_db_id} removed from {INCIDENT_TABLE_NAME}.")
        
        msg = "Incident video deleted."
        if file_removed: msg += " File also removed."
        else: msg += " File was not found on disk."
        return jsonify({"success": True, "message": msg}), 200

    except sqlite3.Error as e:
        db.rollback()
        app.logger.error(f"Delete Incident: SQLite error for ID {incident_db_id}: {e}")
        return jsonify({"success": False, "error": "Database error during deletion"}), 500
    except OSError as e:
        app.logger.error(f"Delete Incident: OS error deleting file {absolute_file_path_on_disk}: {e}")
        return jsonify({"success": False, "error": f"Server error deleting file: {e.strerror}"}), 500
    except Exception as e:
        db.rollback()
        app.logger.error(f"Delete Incident: Unexpected error for ID {incident_db_id}: {e}")
        return jsonify({"success": False, "error": "An unexpected server error occurred"}), 500

'''@app.route('/latest_recordings')
@login_required
def latest_recordings():
    recordings = get_latest_recordings()
    return render_template("latest_recordings.html", recordings=recordings, username=session['username'])'''


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
    host = '0.0.0.0'
    app.run(debug=True, host='0.0.0.0', port=5001)
        