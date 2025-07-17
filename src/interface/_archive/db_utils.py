# src/interface/db_utils.py
import sqlite3
import os
import time
from flask import g

DATABASE_FILENAME = 'video_log.db' # Just the filename

def get_db_path(project_root_path):
    # DB will be at the project root
    return os.path.join(project_root_path, DATABASE_FILENAME)

def get_db(project_root_path):
    db_path = get_db_path(project_root_path)
    if 'db' not in g:
        g.db = sqlite3.connect(
            db_path,
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(exception=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db_schema(project_root_path):
    db_path = get_db_path(project_root_path)
    # Create containing directory if it doesn't exist (though project_root_path should exist)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    db = sqlite3.connect(db_path)
    cursor = db.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            camera_id TEXT NOT NULL,
            timestamp REAL NOT NULL,
            display_timestamp TEXT NOT NULL,
            file_path TEXT NOT NULL UNIQUE, -- Path relative to project_root_path
            is_incident INTEGER DEFAULT 0,
            incident_folder TEXT -- Relative path part if incident, e.g., "cam1_incident_xxx"
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_videos_camera_id_incident ON videos (camera_id, is_incident);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_videos_timestamp ON videos (timestamp);')
    db.commit()
    db.close()
    print(f"Database schema initialized at {db_path}")

def add_video_entry(project_root_path, filename, camera_id, absolute_file_path_on_disk, is_incident=False, incident_folder_name=None):
    db = get_db(project_root_path)
    
    # Path relative to project_root_path
    # e.g., absolute_file_path_on_disk = D:/.../SPL-Alram-P2-OOP_Obj_detect/src/recordings/camera1/video.mp4
    #       project_root_path = D:/.../SPL-Alram-P2-OOP_Obj_detect/
    #       relative_file_path will be "src/recordings/camera1/video.mp4"
    relative_file_path = os.path.relpath(absolute_file_path_on_disk, start=project_root_path)
    relative_file_path = relative_file_path.replace('\\', '/') # Ensure POSIX-style paths

    existing = db.execute('SELECT id FROM videos WHERE file_path = ?', (relative_file_path,)).fetchone()
    if existing:
        return existing['id']

    try:
        raw_ts = os.path.getctime(absolute_file_path_on_disk)
        display_ts = time.ctime(raw_ts)
        cursor = db.execute(
            '''INSERT INTO videos (filename, camera_id, timestamp, display_timestamp, file_path, is_incident, incident_folder)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (filename, camera_id, raw_ts, display_ts, relative_file_path, 1 if is_incident else 0, incident_folder_name)
        )
        db.commit()
        # print(f"Added video to DB: {relative_file_path}")
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        db.rollback()
        existing = db.execute('SELECT id FROM videos WHERE file_path = ?',(relative_file_path,)).fetchone()
        return existing['id'] if existing else None # Should not happen if first check worked
    except sqlite3.Error as e:
        db.rollback(); print(f"DB Error adding {relative_file_path}: {e}")
    except FileNotFoundError:
        print(f"File not found for DB add: {absolute_file_path_on_disk}")
    return None

def get_videos_for_camera(project_root_path, camera_id, is_incident=False):
    db = get_db(project_root_path)
    videos = db.execute(
        '''SELECT id, filename, camera_id, timestamp, display_timestamp, file_path, incident_folder
           FROM videos WHERE camera_id = ? AND is_incident = ? ORDER BY timestamp DESC''',
        (camera_id, 1 if is_incident else 0)
    ).fetchall()
    return videos

def get_video_by_id(project_root_path, video_id):
    db = get_db(project_root_path)
    return db.execute(
        'SELECT id, filename, camera_id, file_path, is_incident, incident_folder FROM videos WHERE id = ?',
        (video_id,)
    ).fetchone()

def delete_video_entry(project_root_path, video_id):
    db = get_db(project_root_path)
    try:
        db.execute('DELETE FROM videos WHERE id = ?', (video_id,)); db.commit()
        return True
    except sqlite3.Error as e:
        db.rollback(); print(f"DB Error deleting video ID {video_id}: {e}")
    return False

def sync_videos_for_camera(project_root_path, camera_id, folder_to_scan_abs, is_incident=False, incident_folder_name_for_db=None):
    if not os.path.isdir(folder_to_scan_abs):
        # print(f"  [Sync] Dir not found: {folder_to_scan_abs}"); 
        return

    videos_on_disk_filenames = set()
    for file in os.listdir(folder_to_scan_abs):
        if file.endswith(".mp4"):
            videos_on_disk_filenames.add(file)
            full_file_path_abs = os.path.join(folder_to_scan_abs, file)
            add_video_entry(project_root_path, file, camera_id, full_file_path_abs, is_incident, incident_folder_name_for_db)

    db = get_db(project_root_path)
    query = 'SELECT id, filename, file_path FROM videos WHERE camera_id = ? AND is_incident = ?'
    params = [camera_id, 1 if is_incident else 0]
    if is_incident: # For incidents, we must match the specific incident_folder
        query += ' AND incident_folder = ?'
        params.append(incident_folder_name_for_db)
    
    videos_in_db_for_context = db.execute(query, tuple(params)).fetchall()

    for db_video in videos_in_db_for_context:
        # db_video['file_path'] is relative to project_root_path. Convert to absolute.
        db_video_abs_path = os.path.join(project_root_path, db_video['file_path'].replace('/', os.sep))
        
        # Check if this DB entry truly belongs to the folder_to_scan_abs
        if os.path.normpath(os.path.dirname(db_video_abs_path)) == os.path.normpath(folder_to_scan_abs):
            if db_video['filename'] not in videos_on_disk_filenames:
                # print(f"  [Sync] DB video '{db_video['filename']}' not on disk in {folder_to_scan_abs}. Removing from DB.")
                delete_video_entry(project_root_path, db_video['id'])