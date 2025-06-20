#add the video metadata to sqlite3 database
#retrieve and playback?
#add lock function from server3.py

#11062025 Sensor_info page added to ubuntu version of server2_db.py

#20062025 New Jetson Nano
#codec changed : acv1 -> mp4v (compared to server2_db_ubuntu.py)
#optimised for web playback using ffmpeg 
#database and sensor page fixes

from flask import Flask, render_template, Response, request, redirect, url_for, jsonify, session, send_from_directory, flash, g
import os
import cv2
import json
from threading import Thread, Event, Lock # For concurrent operations (recording, streaming)
import time
import shutil # For file operations like deleting directories
from functools import wraps # For creating decorators (e.g., login_required)
import atexit
import sqlite3
import logging
import datetime
from dateutil import parser # For parsing ISO 8601 timestamps
import subprocess # <--- ADD THIS LINE


logging.basicConfig(level=logging.INFO)

# Initialize Flask app
app = Flask(__name__, template_folder='static/templates')
app.secret_key = '14a6a86bf47bf75c4479c0c70886b2a5'  # Secret key for session management

# --- Path Configurations ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) # Directory of the current script
# SRC_DIR = SPL-Alram-P2-Database/src/
SRC_DIR = os.path.dirname(SCRIPT_DIR)
# PROJECT_ROOT_DIR = SPL-Alram-P2-Database/
PROJECT_ROOT_DIR = os.path.dirname(SRC_DIR)

SENSOR_DATA_FILE = os.path.join(PROJECT_ROOT_DIR, 'sensor_data.json')

# --- Database Configuration ---
DATABASE_NAME = 'videos.db'
TABLE_NAME = 'video_metadata'
INCIDENT_TABLE_NAME = 'incident_video_metadata'

# DATABASE_PATH = SPL-Alram-P2-Database/videos.db
DATABASE_PATH = os.path.join(PROJECT_ROOT_DIR, DATABASE_NAME)
app.logger.setLevel(logging.INFO) # Set Flask app logger level

'''recordings_dir = "src/recordings"
incidents_dir = "src/incidents"
settings_file = "src/settings.json"'''


# --- Flask Database Helper Functions ---
def get_db():
    #Opens a new database connection if there is none yet for the current application context.
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
    ''') 

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
    ''') 
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

def load_sensor_data():
    """Loads sensor data from the JSON file."""
    if not os.path.exists(SENSOR_DATA_FILE):
        return {}  # Return empty dict if file doesn't exist
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
    """Saves sensor data to the JSON file."""
    try:
        with open(SENSOR_DATA_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        app.logger.info(f"Sensor data saved to {SENSOR_DATA_FILE}")
        return True
    except Exception as e:
        app.logger.error(f"Error saving sensor data to {SENSOR_DATA_FILE}: {e}")
        return False

class Camera:
    #device index=0 -> webcam
    def __init__(self, camera_id, recordings_dir, incidents_dir, settings_file, description="", device_index=0):
        self.camera_id = str(camera_id) # Unique identifier for the camera
        self.recordings_dir = recordings_dir
        self.incidents_dir = incidents_dir
        self.settings_file = settings_file
        self.description = description  # Add description attribute
        self.device_index = device_index # OpenCV device index (e.g., 0 for default webcam)
        
        self.capture = None
        self.capture_lock = Lock()  # Lock for thread-safe access to capture
        self.active_feed_clients = 0  # Counter for active live feed clients

        self.recording = False
        self._stop_recording_event = Event()  # Event to signal recording stop
        self.recording_thread = None
        
        # Ensure directories exist (absolute paths needed for os.makedirs)
        os.makedirs(recordings_dir, exist_ok=True)
        os.makedirs(incidents_dir, exist_ok=True)
        self.load_settings() # Load camera-specific settings

    def _ensure_capture_initialized(self):
        """Initializes and returns the cv2.VideoCapture object if not already done."""
        with self.capture_lock:
            if self.capture is None or not self.capture.isOpened():
                if self.capture is not None: 
                    app.logger.info(f"Releasing stale capture for camera {self.camera_id} before re-initializing.")
                    self.capture.release() # Release stale one
                    self.capture = None
                try:
                    app.logger.info(f"Initializing cv2.VideoCapture for camera {self.camera_id} (device: {self.device_index})...")
                    self.capture = cv2.VideoCapture(self.device_index) # Initialize with device index
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
        """Releases the capture object ONLY if not recording AND no active feed clients."""
        with self.capture_lock:
            if self.capture is not None and self.capture.isOpened():
                if not self.recording and self.active_feed_clients == 0:
                    app.logger.info(f"Releasing capture for camera {self.camera_id} (no recording, no feed clients).")
                    self.capture.release()
                    self.capture = None
                else:
                    app.logger.info(f"Capture for camera {self.camera_id} NOT released. Recording: {self.recording}, Feed Clients: {self.active_feed_clients}")

    def release_capture(self): # Called by atexit or logout for a FULL shutdown
        """Releases the cv2.VideoCapture object if it's initialized. Stops recording if active."""
        app.logger.info(f"FULL release_capture called for camera {self.camera_id}")
        if self.recording:
            # This will internally handle the recording thread and flags
            self.stop_recording_logic(called_from_release_all=True)  # Stop recording first
        
        # Ensure capture is released, even if not "recording" but was somehow left open
        with self.capture_lock:
            if self.capture is not None and self.capture.isOpened():
                app.logger.info(f"Force releasing capture for camera {self.camera_id} during full shutdown.")
                self.capture.release()
                self.capture = None
        self.active_feed_clients = 0 # Reset on full release
        app.logger.info(f"All resources for camera {self.camera_id} should be released after full shutdown sequence.")
    
    def start_recording_thread(self):
        if self.recording and self.recording_thread and self.recording_thread.is_alive():
            app.logger.info(f"Camera {self.camera_id} is already recording.")
            return self.recording_thread

        app.logger.info(f"Attempting to start recording thread for camera {self.camera_id}...")
        if self._ensure_capture_initialized(): # This gets or creates self.capture
            self.recording = True
            self._stop_recording_event.clear()
            
            # Clean up old thread if it exists and is dead
            if self.recording_thread and not self.recording_thread.is_alive():
                self.recording_thread = None
            
            if self.recording_thread and self.recording_thread.is_alive(): # Should not happen if first check passed
                app.logger.warning(f"Warning: Previous recording thread for {self.camera_id} still alive. Joining...")
                self.recording_thread.join(timeout=1.0) # Give it a sec to die

            self.recording_thread = Thread(target=self.record_video, name=f"RecordThread-{self.camera_id}")
            self.recording_thread.daemon = True # Allow main program to exit even if threads are running
            self.recording_thread.start()
            app.logger.info(f"Recording thread started for camera {self.camera_id}.")
            return self.recording_thread
        else:
            app.logger.error(f"Failed to initialize capture for recording on camera {self.camera_id}. Recording not started.")
            self.recording = False # Ensure recording is false if capture fails
            return None

    def stop_recording_logic(self, called_from_release_all=False):
        app.logger.info(f"stop_recording_logic called for camera {self.camera_id}. From release_all: {called_from_release_all}")
        if not self.recording and not (self.recording_thread and self.recording_thread.is_alive()):
            app.logger.info(f"Camera {self.camera_id} not actively recording or thread inactive.")
            if not called_from_release_all: # Only try to release if not part of a full shutdown
                self._release_capture_if_unused()
            return

        self.recording = False # Signal the recording loop to stop
        self._stop_recording_event.set() # Set the event to break loops

        if self.recording_thread and self.recording_thread.is_alive():
            app.logger.info(f"Waiting for recording thread {self.camera_id} to finish...")
            self.recording_thread.join(timeout=5.0) # Increased timeout for writer to finish
            if self.recording_thread.is_alive():
                app.logger.warning(f"Warning: Recording thread {self.camera_id} did not exit cleanly after 5 seconds.")
            else:
                app.logger.info(f"Recording thread {self.camera_id} joined successfully.")
        self.recording_thread = None # Clear the thread reference
        app.logger.info(f"Recording thread for {self.camera_id} stopped/joined.")

        if not called_from_release_all:
            self._release_capture_if_unused() # Attempt to release main capture
        
        app.logger.info(f"Recording stopped for camera {self.camera_id}.")

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
            data.get("description", "") # Get description, default to empty string if not present
        )

    def load_settings(self):
        if not os.path.exists(self.settings_file):
            default_settings = {
                "max_videos": 5, # Max number of normal recordings to kee
                "video_duration": 5,  # Duration of each recording segment in seconds
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
                
                raw_ts_from_file = 0 # Placeholder if file missing
                display_ts_from_file = "N/A (File Missing)"
                if file_exists:
                    try:
                        raw_ts_from_file = os.path.getctime(absolute_file_path) #file creation time
                        display_ts_from_file = time.ctime(raw_ts_from_file) # Convert to human-readable format
                    except Exception as e:
                        app.logger.warning(f"Error getting ctime for {absolute_file_path}: {e}")
                
                videos.append({
                    "id": row["id"],
                    "filename": row["filename"],
                    "timestamp": row["timestamp"], # DB timestamp (YYYYMMDD-HHMMSS)
                    "display_timestamp": display_ts_from_file, 
                    "raw_timestamp": raw_ts_from_file, 
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
                
                display_ts = row["incident_trigger_timestamp"] # Use the trigger timestamp
                if not file_exists:
                    display_ts += " (File Missing)"

                incident_videos.append({
                    "id": row["id"], # DB ID of the incident video entry
                    "filename": row["original_video_filename"], # The name of the video file
                    "incident_folder": row["incident_folder_name"],
                    "timestamp": row["incident_trigger_timestamp"], # ISO 8601 UTC
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
        #Core recording loop. Captures video segments and saves them.

        app.logger.info(f"Record_video loop started for camera {self.camera_id}.")
        while self.recording and not self._stop_recording_event.is_set():
            # Timestamp for database (UTC, ISO 8601 format)
            db_timestamp_to_store = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
            
            # Filename generation (local time for human-readable filenames)
            filename_timestamp_part = time.strftime("%Y%m%d-%H%M%S") 
            filename = f"cam{str(self.camera_id)}_{filename_timestamp_part}.mp4"
            relative_path_for_db = os.path.join(self.recordings_dir, filename)
            absolute_filepath_for_cv = os.path.join(PROJECT_ROOT_DIR, relative_path_for_db)
            os.makedirs(os.path.dirname(absolute_filepath_for_cv), exist_ok=True) # Ensure directory exists

            fourcc = cv2.VideoWriter_fourcc(*"mp4v") # Codec (H.264)
            if not hasattr(self, 'settings') or not self.settings: self.load_settings()
            video_duration_seconds = self.settings.get("video_duration", 5)
            fps = 15
            out = None # Initialize out here

            # --- Critical section for VideoWriter setup ---
            try:
                with self.capture_lock: # Protect access to self.capture for getting properties
                    if not self.capture or not self.capture.isOpened():
                        app.logger.error(f"Cam {self.camera_id}: SHARED capture became unopened. Stopping recording.")
                        self.recording = False # Signal to stop
                        break # Exit outer while loop

                    frame_width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
                    frame_height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    if frame_width == 0 or frame_height == 0:
                        app.logger.error(f"Cam {self.camera_id}: Invalid frame dimensions ({frame_width}x{frame_height}). Skipping segment.")
                        time.sleep(1); continue # Skip this segment

                # VideoWriter can be created outside the lock if its parameters are now fixed
                app.logger.info(f"Cam {self.camera_id}: Writing to {absolute_filepath_for_cv} ({frame_width}x{frame_height}@{fps}fps)")
                out = cv2.VideoWriter(absolute_filepath_for_cv, fourcc, fps, (frame_width, frame_height))
                if not out.isOpened():
                    app.logger.error(f"Cam {self.camera_id}: Failed to open VideoWriter for {absolute_filepath_for_cv}"); time.sleep(1); continue
            except Exception as e:
                app.logger.error(f"Cam {self.camera_id}: Error creating VideoWriter: {e}"); time.sleep(1); continue
            # --- End critical section for VideoWriter setup ---

            if out and out.isOpened(): # Ensure out was successfully created
                frame_count = 0
                total_frames_to_record = int(fps * video_duration_seconds)

                # --- Segment recording loop ---
                while self.recording and not self._stop_recording_event.is_set() and frame_count < total_frames_to_record:
                    frame_read_successfully = False
                    frame_data = None
                    with self.capture_lock: # Lock for reading the frame from shared capture
                        if not self.capture or not self.capture.isOpened():
                            app.logger.error(f"Cam {self.camera_id}: SHARED capture lost during segment write.")
                            self.recording = False; break # Break inner loop
                        ret, current_frame = self.capture.read()
                        if ret:
                            frame_data = current_frame # No copy needed if just writing
                            frame_read_successfully = True
                    
                    if self._stop_recording_event.is_set(): break # Check event again after lock

                    if frame_read_successfully and frame_data is not None:
                        out.write(frame_data); frame_count += 1
                    elif self.recording: # Only log warning if we are supposed to be recording
                        app.logger.warning(f"Cam {self.camera_id}: Failed to read frame from shared capture during RECORDING segment."); time.sleep(0.05)
                # If recording stopped externally (e.g., stop_recording call)                
                if not self.recording or self._stop_recording_event.is_set(): # If outer loop broke due to recording flag
                    if out.isOpened(): out.release() # Ensure writer is released
                    break # Break from the segment writing loop too

                out.release() # Release writer for this segment
                app.logger.info(f"Cam {self.camera_id}: Wrote {frame_count} frames to {filename}.")
                
                if frame_count > 0:
                    # --- NEW CODE BLOCK TO FIX MP4V FOR WEB ---
                    try:
                        # We will create a temporary fixed file and then replace the original
                        # This is safer than trying to edit in-place.
                        temp_output_path = absolute_filepath_for_cv + ".temp.mp4"
                        
                        # The ffmpeg command:
                        # -i: input file
                        # -c:v copy: copy the video stream without re-encoding (FAST!)
                        # -movflags +faststart: This is the magic flag that moves the moov atom
                        command = [
                            'ffmpeg',
                            '-i', absolute_filepath_for_cv,
                            '-c:v', 'libx264',           # Re-encode video to H.264 (very compatible)
                            '-preset', 'veryfast',       # Use a fast encoding preset to not slow down the server
                            '-c:a', 'aac',               # Encode audio to AAC (standard for web)
                            '-movflags', '+faststart',   # Keep this - it's still essential
                            '-y',                        # Overwrite output file if it exists
                            temp_output_path
                        ]
                        app.logger.info(f"Cam {self.camera_id}: Running ffmpeg to web-optimize the video...")
                        # We use capture_output=True and text=True to hide ffmpeg's verbose output from the main console
                        # but still be able to log it if there's an error.
                        result = subprocess.run(command, check=True, capture_output=True, text=True)
                        
                        # If ffmpeg succeeded, replace the original file with the fixed one
                        os.replace(temp_output_path, absolute_filepath_for_cv)
                        app.logger.info(f"Cam {self.camera_id}: Successfully web-optimized {filename}.")

                    except subprocess.CalledProcessError as e:
                        # If ffmpeg fails, log the error and keep the original (unplayable in web) file
                        app.logger.error(f"Cam {self.camera_id}: ffmpeg failed for {filename}. Stderr: {e.stderr}")
                    except FileNotFoundError:
                        app.logger.error("ffmpeg command not found. Please ensure ffmpeg is installed and in the system's PATH.")

                if frame_count > 0:
                    # Store the ISO timestamp in the DB
                    self.insert_video_metadata(filename, db_timestamp_to_store, relative_path_for_db)                    
                    self.prune_videos_from_database()
                else:
                    app.logger.warning(f"Cam {self.camera_id}: No frames recorded for {filename}. Not saving to DB or pruning.")
                    if os.path.exists(absolute_filepath_for_cv): # Delete empty file
                        try:
                            os.remove(absolute_filepath_for_cv)
                            app.logger.info(f"Cam {self.camera_id}: Removed empty/failed file {absolute_filepath_for_cv}")
                        except OSError as e_del:
                            app.logger.error(f"Cam {self.camera_id}: Error removing empty/failed file {absolute_filepath_for_cv}: {e_del}")
            
            if self.recording and not self._stop_recording_event.is_set():
                time.sleep(0.01) # Short sleep between segments

        app.logger.info(f"Record_video loop EXITED for camera {self.camera_id}. Final recording state: {self.recording}, Event set: {self._stop_recording_event.is_set()}")
    
    def prune_videos_from_database(self):
        """Prunes oldest videos for this camera based on settings, using the database."""
        conn = None
        try:
            if not hasattr(self, 'settings') or not self.settings: self.load_settings()
            max_videos = self.settings.get("max_videos", 5)  # Default if not set

            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Select videos for this camera, ordered oldest first (ASC timestamp)
            query_select = f"SELECT id, path FROM {TABLE_NAME} WHERE camera_id = ? ORDER BY timestamp ASC" # Oldest first
            cursor.execute(query_select, (str(self.camera_id),))
            all_camera_videos_in_db = cursor.fetchall()

            num_videos_in_db = len(all_camera_videos_in_db)
            app.logger.info(f"[PruneDB Cam {self.camera_id}] Found {num_videos_in_db} videos. Max: {max_videos}")

            if num_videos_in_db > max_videos:
                num_to_delete = num_videos_in_db - max_videos
                videos_to_delete = all_camera_videos_in_db[:num_to_delete] # Get the oldest ones
                app.logger.info(f"[PruneDB Cam {self.camera_id}] Deleting {len(videos_to_delete)} oldest video(s).")

                for video_entry in videos_to_delete:
                    db_id_to_delete = video_entry["id"]
                    relative_path = video_entry["path"]
                    absolute_file_path_to_delete = os.path.join(PROJECT_ROOT_DIR, relative_path)

                    # Delete physical file
                    if os.path.exists(absolute_file_path_to_delete):
                        try:
                            os.remove(absolute_file_path_to_delete)
                            app.logger.info(f"[PruneDB Cam {self.camera_id}] Deleted file: {absolute_file_path_to_delete}")
                        except OSError as e:
                            app.logger.error(f"[PruneDB Cam {self.camera_id}] Error deleting file {absolute_file_path_to_delete}: {e}")
                    else:
                        app.logger.warning(f"[PruneDB Cam {self.camera_id}] File {absolute_file_path_to_delete} not found for (DB ID: {db_id_to_delete})")
                    
                    # Delete DB entry                    
                    query_delete_db = f"DELETE FROM {TABLE_NAME} WHERE id = ?"
                    cursor.execute(query_delete_db, (db_id_to_delete,))
                conn.commit()
                app.logger.info(f"[PruneDB Cam {self.camera_id}] DB entries deleted.")
        except sqlite3.Error as e:
            if conn: conn.rollback() # Rollback on DB error
            app.logger.error(f"[PruneDB Cam {self.camera_id}] SQLite Error: {e}")
        except Exception as e:
            app.logger.error(f"[PruneDB Cam {self.camera_id}] General Error: {e}")
        finally:
            if conn: conn.close()

        
    def insert_video_metadata(self, filename, timestamp_str, relative_path_to_project_root):
        """Inserts video metadata into the database.
        'relative_path_to_project_root' :  'src/recordings/camera1/file.mp4'
        """
        conn = None
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            query = f"""
                INSERT INTO {TABLE_NAME} (camera_id, filename, timestamp, path)
                VALUES (?, ?, ?, ?)
            """
            # timestamp_str should be ISO 8601 UTC
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
            # incident_trigger_ts and original_ts should be ISO 8601 UTC
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
        conn = sqlite3.connect('videos.db') # Should use DATABASE_PATH
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM video_metadata WHERE camera_id = ?', (camera_id,))
        videos = cursor.fetchall()
        conn.close()
        return videos

    
        
    def simulate_incident(self):
        """Simulates an incident: creates an incident folder and copies recent normal recordings to it."""
        # Timestamp for the incident event itself (UTC, ISO 8601)
        incident_trigger_iso_timestamp_for_db = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()        
        incident_folder_timestamp_part = time.strftime("%Y%m%d-%H%M%S")
        incident_folder_name_only = f"cam{str(self.camera_id)}_incident_{incident_folder_timestamp_part}"
        
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
            original_timestamp_from_db = video_data["timestamp"] # ISO 8601 UTC from normal recording DB

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
                        incident_trigger_iso_timestamp_for_db, # Timestamp of incident creation
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
        app.logger.info(f"Attempting to generate live feed for camera {self.camera_id}")

        local_capture_ref = self._ensure_capture_initialized()  # Ensures shared capture is ready

        if not local_capture_ref or not local_capture_ref.isOpened():  # Check the reference returned
            app.logger.error(f"Error: Could not get/initialize shared capture for LIVE FEED on camera {self.camera_id}")
            return 

        with self.capture_lock:  # Increment client count under lock
            self.active_feed_clients += 1
        app.logger.info(f"Successfully using shared capture for LIVE FEED on camera {self.camera_id}. Active clients: {self.active_feed_clients}")

        try:
            while True: 
                frame_data = None
                frame_available = False
                with self.capture_lock:  # Lock for reading from shared capture
                    if not self.capture or not self.capture.isOpened():  # Check shared self.capture
                        app.logger.error(f"Error: Camera {self.camera_id} SHARED capture lost during LIVE FEED.")
                        break  # Exit loop if capture is lost
                    ret, current_frame = self.capture.read()
                    if not ret or current_frame is None or current_frame.size == 0:
                        app.logger.debug(f"Captured frame shape: {current_frame.shape}")
                        app.logger.debug(f"Frame channels: {current_frame.shape}")
                        app.logger.error(f"Failed to read frame from camera {self.camera_id}.")
                        continue  # Skip this iteration

                    self.capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
                    new_width = 640
                    new_height = 480
                    self.capture.set(cv2.CAP_PROP_FPS, 15)
                    try:
                        current_frame = cv2.resize(current_frame, (new_width, new_height))  # Correct usage of cv2.resize
                        #current_frame = cv2.cvtColor(current_frame, cv2.COLOR_BGR2RGB)
                        #app.logger.info(f"Frame resized to: {new_width}x{new_height}")
                    except Exception as e:
                        app.logger.error(f"Error resizing frame: {e}. Skipping frame.")
                        continue  # Skip this frame if resizing fails
                    
                    frame_data = current_frame.copy()  # Important: copy the frame for further processing
                    frame_available = True                        

                if not frame_available:
                    app.logger.warning(f"Warning: Could not read frame from camera {self.camera_id} (shared capture) for LIVE FEED.")
                    time.sleep(0.1)  # Avoid busy-looping
                    continue  # Try reading again

                # Add recording indicator (red rectangle) if camera is currently recording                
                if self.recording:  # Check recording status
                    cv2.rectangle(frame_data, (0, 0), (frame_data.shape[1] - 1, frame_data.shape[0] - 1), (0, 0, 255), 10)  # BGR color

                # Encode the frame
                _, buffer = cv2.imencode(".jpg", frame_data)
                if not _:
                    app.logger.warning(f"Warning: cv2.imencode failed for LIVE FEED camera {self.camera_id}")
                    continue
                frame_bytes = buffer.tobytes()

                # Yield frame for MJPEG stream
                try:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")
                except GeneratorExit:  # Client disconnected
                    app.logger.info(f"Client disconnected from live feed for camera {self.camera_id}.")
                    break 
                except Exception as e_yield:  # Other error during yield
                    app.logger.error(f"Error yielding frame for camera {self.camera_id} live feed: {e_yield}")
                    break
                time.sleep(1/30)  # Approximate frame rate control (e.g., 30fps)
        except Exception as e_feed:
            app.logger.error(f"Exception in generate_video_feed for camera {self.camera_id}: {e_feed}")
        finally:
            with self.capture_lock:  # Decrement client count and attempt to release capture if no longer needed
                self.active_feed_clients -= 1
            app.logger.info(f"Live feed generation ended for camera {self.camera_id}. Active clients: {self.active_feed_clients}")
            self._release_capture_if_unused()  # Attempt to release capture if no longer needed


def format_display_timestamp_sgt(iso_timestamp_str_utc):
        """Converts UTC ISO timestamp string to SGT display string."""
        if not iso_timestamp_str_utc:
            return "N/A" # Handle empty or None timestamps
        try: 
            dt_object_utc = parser.isoparse(iso_timestamp_str_utc) # Parse the ISO string
            # Ensure it's UTC 
            if dt_object_utc.tzinfo is None or dt_object_utc.tzinfo.utcoffset(dt_object_utc) is None:
                dt_object_utc = dt_object_utc.replace(tzinfo=datetime.timezone.utc)
            else:
                dt_object_utc = dt_object_utc.astimezone(datetime.timezone.utc) #Convert to UTC if already aware
            
            # Define SGT timezone (UTC+8)
            sgt_timezone = datetime.timezone(datetime.timedelta(hours=8))
            dt_object_sgt = dt_object_utc.astimezone(sgt_timezone)
            
            # Format for display
            return dt_object_sgt.strftime("%Y-%m-%d %H:%M:%S SGT") 
            # Or for "Mon May 26 10:22:30 2025 SGT" format:
            # return dt_object_sgt.strftime("%a %b %d %H:%M:%S %Y SGT") 
        except Exception as e:
            app.logger.warning(f"Could not parse/convert incident timestamp: '{iso_timestamp_str_utc}'. Error: {e}")
            return iso_timestamp_str_utc # Fallback to showing the raw string


def save_cameras_to_json():
         #Saves the current 'cameras' dictionary (Camera objects) to 'cameras.json'
        camera_data = {camera_id: camera.to_dict() for camera_id, camera in cameras.items()}
        print("Saving cameras to JSON:", camera_data)  # Debugging line
        with open('cameras.json', 'w') as f:
            json.dump(camera_data, f)
        print("Cameras saved successfully.")
    
def load_cameras_from_json():
    global cameras
    cameras = {} # Initialize/clear existing cameras
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

# --- User Authentication ---
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
             
        users = data.get('users', []) # Get the list of users
        # Check if any user in the list matches the provided username and password
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
        if not session.get('authenticated'): # Check session for authentication flag
            return redirect(url_for('login')) # Redirect to login page if not authenticated
        return f(*args, **kwargs) # Proceed to the original function if authenticated
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
            session['authenticated'] = True # Set session flag
            session['username'] = username # Store username in session
            return redirect(url_for('camera_list')) # Redirect to camera list on successful login
        error = 'Invalid credentials. Please try again.'
    return render_template('login.html', error = error) # Display login form with error if any

@app.route('/index/<camera_id>')
@login_required
def index(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        return "Camera not found", 404
    
    # Get recording status from session to reflect current state on page load
    recording_status = session.get('recording_status', 'stopped')  # Default to 'stopped'
    return render_template('index.html', username=session['username'], camera_id=camera_id, description=camera.description, recording_status=recording_status)

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
        
        next_id_int = 1 # Determine the next available integer ID for the camera
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
                cameras[camera_id_key] = new_camera # Add to in-memory dictionary
                save_cameras_to_json()  # Save the updated cameras to JSON
                return redirect(url_for('camera_list'))
            except Exception as e: 
                print(f"Error creating camera {camera_id_key}: {e}")
                error = "An unexpected error occurred while adding the camera."

    return render_template('add_camera.html', error =error)

#Delete camera
@app.route('/delete_camera/<camera_id>', methods=['POST'])
@login_required
def delete_camera(camera_id):
    if camera_id in cameras:
        camera_to_delete = cameras[camera_id]
        camera_to_delete.release_capture() # Release hardware resources first

        # Get paths before deleting the camera object from the 'cameras' dictionary
        recordings_path_to_remove = camera_to_delete.recordings_dir 
        incidents_path_to_remove = camera_to_delete.incidents_dir
        settings_path_to_remove = camera_to_delete.settings_file
        
        camera_id_for_db_and_sensor = str(camera_to_delete.camera_id) # Consistent key

        try:
            # 1. Delete from the 'cameras' in-memory dictionary and JSON file
            del cameras[camera_id_for_db_and_sensor] # Use the string ID here too for consistency
            save_cameras_to_json()
            app.logger.info(f"Camera object for ID {camera_id_for_db_and_sensor} removed from memory and cameras.json.")

            # 2. Delete physical folders and files (using absolute paths)
            absolute_recordings_path = os.path.join(PROJECT_ROOT_DIR, recordings_path_to_remove)
            if os.path.exists(absolute_recordings_path):
                shutil.rmtree(absolute_recordings_path)
                app.logger.info(f"Successfully deleted recordings directory: {absolute_recordings_path}")
            else:
                app.logger.warning(f"Recordings directory not found, skipping deletion: {absolute_recordings_path}")

            absolute_incidents_path = os.path.join(PROJECT_ROOT_DIR, incidents_path_to_remove)
            if os.path.exists(absolute_incidents_path):
                shutil.rmtree(absolute_incidents_path)
                app.logger.info(f"Successfully deleted incidents directory: {absolute_incidents_path}")
            else:
                app.logger.warning(f"Incidents directory not found, skipping deletion: {absolute_incidents_path}")

            absolute_settings_path = os.path.join(PROJECT_ROOT_DIR, settings_path_to_remove)
            if settings_path_to_remove and os.path.exists(absolute_settings_path):
                os.remove(absolute_settings_path)
                app.logger.info(f"Successfully deleted settings file: {absolute_settings_path}")
            else:
                app.logger.warning(f"Settings file not found or path invalid, skipping deletion: {absolute_settings_path}")


            # 3. Delete corresponding video metadata from the database
            conn_db = None # Renamed to avoid conflict with outer 'conn' if it existed
            try:
                conn_db = sqlite3.connect(DATABASE_PATH)
                cursor = conn_db.cursor()
                
                query_delete_recordings = f"DELETE FROM {TABLE_NAME} WHERE camera_id = ?"
                cursor.execute(query_delete_recordings, (camera_id_for_db_and_sensor,))
                deleted_recordings_count = cursor.rowcount
                app.logger.info(f"Deleted {deleted_recordings_count} entries from {TABLE_NAME} for cam_id: {camera_id_for_db_and_sensor}")

                query_delete_incidents = f"DELETE FROM {INCIDENT_TABLE_NAME} WHERE camera_id = ?"
                cursor.execute(query_delete_incidents, (camera_id_for_db_and_sensor,))
                deleted_incidents_count = cursor.rowcount
                app.logger.info(f"Deleted {deleted_incidents_count} entries from {INCIDENT_TABLE_NAME} for cam_id: {camera_id_for_db_and_sensor}")
                
                conn_db.commit()
            except sqlite3.Error as e:
                if conn_db: conn_db.rollback()
                app.logger.error(f"DB error deleting metadata for cam_id {camera_id_for_db_and_sensor}: {e}")
            finally:
                if conn_db: conn_db.close()


            # 4. <<< Delete sensor information from sensor_data.json >>>
            all_sensor_data = load_sensor_data()
            if camera_id_for_db_and_sensor in all_sensor_data:
                del all_sensor_data[camera_id_for_db_and_sensor]
                if save_sensor_data(all_sensor_data):
                    app.logger.info(f"Sensor data for camera ID {camera_id_for_db_and_sensor} deleted from {SENSOR_DATA_FILE}.")
                else:
                    app.logger.error(f"Failed to save {SENSOR_DATA_FILE} after deleting sensor data for camera ID {camera_id_for_db_and_sensor}.")
                    flash(f"Warning: Could not update sensor data file after deleting sensor info for camera {camera_id_for_db_and_sensor}.", "warning")
            else:
                app.logger.info(f"No sensor data found for camera ID {camera_id_for_db_and_sensor} in {SENSOR_DATA_FILE}, skipping sensor data deletion.")

            flash(f"Camera {camera_id_for_db_and_sensor} and all associated data (including sensor info) have been deleted.", "success")
            return redirect(url_for('camera_list'))

        except OSError as e:
            app.logger.error(f"OS Error deleting files/folders for camera {camera_id_for_db_and_sensor}: {e}")
            flash(f"Error deleting files/folders for camera {camera_id_for_db_and_sensor}.", "error")
            return redirect(url_for('camera_list'))
        except Exception as e:
            app.logger.error(f"Unexpected error deleting camera {camera_id_for_db_and_sensor}: {e}")
            flash(f"An unexpected error occurred while deleting camera {camera_id_for_db_and_sensor}.", "error")
            return redirect(url_for('camera_list'))

    flash(f"Camera {camera_id} not found.", "warning")
    return "Camera not found", 404 # Or redirect(url_for('camera_list'))



@app.route('/feed_view')
#Placeholder for feed view, can be used to show live feeds or camera status
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
                    camera.release_capture() # Force release
                except Exception as e:
                    print(f"Error releasing camera {camera_id} during logout: {e}")
        print("Finished attempting camera releases for logout.")
    else:
        print("No 'cameras' dictionary found or it's empty during logout.")
    
    session.clear()
    return redirect(url_for('login'))


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

    # Whitelist for incident video sort columns (maps query param to DB column name)
    allowed_sort_columns_videos = {'id': 'id', 'camera_id': 'camera_id', 'filename': 'filename', 'timestamp': 'timestamp'}
    db_column_to_sort = allowed_sort_columns_videos.get(sort_by_param, 'timestamp') # Fallback to 'timestamp'
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
        cursor.execute(query, (str(camera_id),)) # camera_id is string
        videos_from_db = cursor.fetchall() # List of sqlite3.Row objects
        
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
                "display_timestamp": format_display_timestamp_sgt(row["timestamp"]), 
                "path": row["path"]
            })

    except sqlite3.Error as e:
        app.logger.error(f"DB error fetching videos for cam {camera_id} in main app: {e}")
        videos_for_template = []
        flash("Error loading videos from database.", "error")

    app.logger.debug(f"Main app /videos/{camera_id}: Displaying {len(videos_for_template)} videos.")
    return render_template("videos.html", 
                           videos=videos_for_template, 
                           camera_id=camera_id, 
                           username=session['username'], 
                           description=camera.description,
                           current_sort_by=sort_by_param, # Pass for sort arrows
                           current_sort_order=sort_order_param)
# Whitelist for incident video sort columns (maps query param to DB column name)
ALLOWED_SORT_COLUMNS_INCIDENTS = { 
    'id': 'id',
    'camera_id': 'camera_id',
    'filename': 'original_video_filename', 
    'folder': 'incident_folder_name',
    'timestamp': 'incident_trigger_timestamp' # main timestamp for incidents
}

@app.route('/incident_videos/<camera_id>')
@login_required
def incident_videos(camera_id): # Route function name is fine
    camera = cameras.get(camera_id)
    if camera is None:
        flash(f"Camera {camera_id} not found.", "error")
        return redirect(url_for('camera_list'))
    
    sort_by_param = request.args.get('sort_by', 'timestamp') # Default sort by incident trigger timestamp
    sort_order_param = request.args.get('sort_order', 'desc') # Default: descending

    db_column_to_sort = ALLOWED_SORT_COLUMNS_INCIDENTS.get(sort_by_param, 'incident_trigger_timestamp')
    sql_sort_order = 'DESC' if sort_order_param.lower() == 'desc' else 'ASC'

    # Secondary sort for consistent ordering if primary sort values are the same
    order_by_clause = f"{db_column_to_sort} {sql_sort_order}"

    conn = get_db()
    cursor = conn.cursor()
    
    if db_column_to_sort == 'incident_trigger_timestamp':
        order_by_clause += f", original_video_filename {sql_sort_order}" # Sort filename in same direction
    else:
        order_by_clause += f", id {sql_sort_order}" # Default secondary sort by DB ID

    # Build the query with sorting
    query = f"""
        SELECT id, original_video_filename, incident_folder_name, incident_trigger_timestamp, path 
        FROM {INCIDENT_TABLE_NAME} 
        WHERE camera_id = ? 
        ORDER BY {order_by_clause} 
    """
    try:
        cursor.execute(query, (str(camera_id),))
        raw_incident_videos_list = cursor.fetchall()
    except sqlite3.Error as e:
        app.logger.error(f"DB error fetching incidents for cam {camera_id} in main app: {e}")
        raw_incident_videos_list = []
        flash("Error loading incident videos from database.", "error")
    
    # Prepare data for the template, including formatted display timestamp
    incident_videos_for_template = []
    if raw_incident_videos_list:
        for incident_row in raw_incident_videos_list:
            incident_videos_for_template.append({
                "id": incident_row["id"],
                "filename": incident_row["original_video_filename"], 
                "incident_folder": incident_row["incident_folder_name"],
                "timestamp": incident_row["incident_trigger_timestamp"],  # Raw ISO 8601 UTC
                "display_timestamp": format_display_timestamp_sgt(incident_row["incident_trigger_timestamp"]), # Formatted
                "path": incident_row["path"]
            })
    
    app.logger.debug(f"Main app /incident_videos/{camera_id}: Displaying {len(incident_videos_for_template)} incidents with ORDER BY {order_by_clause}")
    return render_template("incident_vid.html", 
                           incident_videos=incident_videos_for_template, 
                           camera_id=camera_id, 
                           username=session['username'], 
                           description=camera.description,
                           current_sort_by=sort_by_param,
                           current_sort_order=sort_order_param
                           )

@app.route('/settings_page/<camera_id>')
@login_required
def settings_page(camera_id):
    camera = cameras.get(camera_id)
    if camera is None:
        return "Camera not found", 404
    current_settings = {}
    try:
        # Attempt to load current settings from the camera's settings file
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
    
    # Validate inputs
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
        # Reload settings page with current (old) settings displayed
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
        current_settings = camera.settings # Show existing settings on error
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

    if camera.start_recording_thread():
        session['recording_status'] = 'recording'  # Store status in session
        return "Recording started", 200
    else:
        return "Failed to start recording (camera init issue?)", 500


@app.route('/stop_recording/<camera_id>', methods=['POST'])
@login_required
def stop_recording(camera_id):
    camera = cameras.get(camera_id) 
    if camera is None:
        return "Camera not found", 404  

    camera.stop_recording_logic()
    session['recording_status'] = 'stopped'  # Update status in session
    return "Recording stopped", 200


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
    video_data = cursor.fetchone() # Fetches as a Row object

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
        #return "Error serving video", 500

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
    filename_on_disk = os.path.basename(relative_path_from_db)  # actual filename on disk
    
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
    
    # First, get the path to delete the file
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
        # Attempt to delete the physical file
        if os.path.exists(absolute_file_path_on_disk):
            os.remove(absolute_file_path_on_disk)
            app.logger.info(f"Delete: File removed: {absolute_file_path_on_disk}")
            file_removed_from_disk = True
        else:
            app.logger.warning(f"Delete: File {absolute_file_path_on_disk} (DB ID: {video_db_id}) not on disk.")
        
        # Delete the database entry
        query_delete_db = f"DELETE FROM {TABLE_NAME} WHERE id = ?"
        cursor.execute(query_delete_db, (video_db_id,))
        db.commit()
        app.logger.info(f"Delete: DB entry ID {video_db_id} removed.")
        
        msg = "Video deleted."
        if file_removed_from_disk: msg += " File also removed."
        else: msg += " File was not found on disk."
        return jsonify({"success": True, "message": msg}), 200

    except sqlite3.Error as e:
        db.rollback()  # Rollback DB changes on error
        app.logger.error(f"Delete: SQLite error for ID {video_db_id}: {e}")
        return jsonify({"success": False, "error": "Database error during deletion"}), 500
    except OSError as e:
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
    # Attempt to delete the physical file
    try:
        if os.path.exists(absolute_file_path_on_disk):
            os.remove(absolute_file_path_on_disk)
            app.logger.info(f"Delete Incident: File removed: {absolute_file_path_on_disk}")
            file_removed = True
        else:
            app.logger.warning(f"Delete Incident: File {absolute_file_path_on_disk} (DB ID: {incident_db_id}) not on disk.")
        
        # Delete the database entry
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

@app.route('/sensor_info/<camera_id>', methods=['GET', 'POST'])
@login_required
def sensor_info_page(camera_id):
    camera = cameras.get(camera_id)
    if not camera:
        flash(f"Camera {camera_id} not found.", "error")
        return redirect(url_for('camera_list'))

    all_sensor_data = load_sensor_data()
    current_sensor_details = all_sensor_data.get(str(camera_id), {}) # Ensure camera_id is string for dict key

    if request.method == 'POST':
        # Handle saving new/updated sensor info
        sensor_type = request.form.get('sensor_type', '').strip()
        sensor_location = request.form.get('sensor_location', '').strip()
        sensor_notes = request.form.get('sensor_notes', '').strip()
        # Add more fields as needed

        if not sensor_type and not sensor_location and not sensor_notes: # Basic validation: at least one field should be filled
            flash("Please provide some information for the sensor.", "warning")
        else:
            updated_details = {
                "type": sensor_type,
                "location": sensor_location,
                "notes": sensor_notes
                # Add other fields from form here
            }
            all_sensor_data[str(camera_id)] = updated_details
            if save_sensor_data(all_sensor_data):
                flash(f"Sensor information for Camera {camera.description} ({camera_id}) updated successfully.", "success")
                current_sensor_details = updated_details # Update for immediate display
            else:
                flash(f"Failed to save sensor information for Camera {camera.description} ({camera_id}).", "error")
        # It's often better to redirect after POST to avoid form resubmission issues
        return redirect(url_for('sensor_info_page', camera_id=camera_id))


    # Fetch incident trigger timestamps from the database for this camera
    incident_timestamps_list = []
    total_recordings = 0
    total_incident_clips = 0 # Renamed for clarity from total_incidents
    conn = get_db()
    cursor = conn.cursor()

    # Get total normal recordings count
    try:
        query_recordings_count = f"SELECT COUNT(id) FROM {TABLE_NAME} WHERE camera_id = ?"
        cursor.execute(query_recordings_count, (str(camera_id),))
        count_result = cursor.fetchone()
        if count_result:
            total_recordings = count_result[0]
    except sqlite3.Error as e:
        app.logger.error(f"DB error fetching recordings count for cam {camera_id}: {e}")
        flash("Error loading recordings count.", "error")

    # Get total incident clips count
    try:
        query_incidents_count = f"SELECT COUNT(id) FROM {INCIDENT_TABLE_NAME} WHERE camera_id = ?"
        cursor.execute(query_incidents_count, (str(camera_id),))
        count_result = cursor.fetchone()
        if count_result:
            total_incident_clips = count_result[0]
    except sqlite3.Error as e:
        app.logger.error(f"DB error fetching incident clips count for cam {camera_id}: {e}")
        flash("Error loading incident clips count.", "error")

    # Select distinct timestamps to avoid listing the same trigger time multiple times if multiple videos were saved for one incident trigger
    query = f"""
        SELECT DISTINCT incident_trigger_timestamp 
        FROM {INCIDENT_TABLE_NAME} 
        WHERE camera_id = ? 
        ORDER BY incident_trigger_timestamp DESC
    """
    try:
        cursor.execute(query, (str(camera_id),))
        raw_timestamps = cursor.fetchall()
        for row in raw_timestamps:
            incident_timestamps_list.append({
                "raw_iso": row["incident_trigger_timestamp"],
                "display_sgt": format_display_timestamp_sgt(row["incident_trigger_timestamp"])
            })
    except sqlite3.Error as e:
        app.logger.error(f"DB error fetching incident timestamps for sensor page (cam {camera_id}): {e}")
        flash("Error loading incident timestamps.", "error")

    return render_template('sensor_info.html',
                           username=session['username'],
                           camera_id=camera_id,
                           camera_description=camera.description,
                           sensor_details=current_sensor_details,
                           incident_timestamps=incident_timestamps_list,
                           total_recordings=total_recordings,               
                           total_incident_clips=total_incident_clips)


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
    host = '0.0.0.0' # Listen on all available network interfaces
    app.run(debug=True, host='0.0.0.0', port=5001) # Port for the Flask development server
        