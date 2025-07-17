# Place this in SPL-Alram-P2-Database/setup_project_db.py (or similar name)
import sqlite3
import os

# Define the project root directory
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__)) # Assumes this script is in the project root

DB_NAME = os.path.join(PROJECT_ROOT, 'video.db') # Database at project root

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Adjust table and column names if yours are different
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id INTEGER,
            filename TEXT,
            timestamp TEXT,
            path TEXT UNIQUE
        )
    ''')
    conn.commit()
    conn.close()
    print(f"Database {DB_NAME} initialized.")

def add_sample_data():
    # Paths are relative to PROJECT_ROOT
    sample_data = [
        (1, 'cam1_20250522-145900.mp4', '20250522-145900', 'src/recordings/camera1/cam1_20250522-145900.mp4'),
        (1, 'cam1_20250522-145906.mp4', '20250522-145906', 'src/recordings/camera1/cam1_20250522-145906.mp4'),
        (2, 'cam2_20250522-154156.mp4', '20250522-154156', 'src/recordings/camera2/cam2_20250522-154156.mp4'),
        (2, 'cam2_20250522-154200.mp4', '20250522-154200', 'src/recordings/camera2/cam2_20250522-154200.mp4'),
    ]
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    for cam_id, fname, ts, path_in_db in sample_data:
        # Create dummy files if they don't exist for testing
        # The path_in_db is 'src/recordings/cameraX/file.mp4'
        full_disk_path = os.path.join(PROJECT_ROOT, path_in_db)
        os.makedirs(os.path.dirname(full_disk_path), exist_ok=True)
        if not os.path.exists(full_disk_path):
            with open(full_disk_path, 'w') as f:
                f.write("dummy video content for " + fname)
            print(f"Created dummy file: {full_disk_path}")

        try:
            cursor.execute("INSERT INTO recordings (camera_id, filename, timestamp, path) VALUES (?, ?, ?, ?)",
                           (cam_id, fname, ts, path_in_db))
        except sqlite3.IntegrityError:
            print(f"Skipping duplicate path: {path_in_db}")

    conn.commit()
    conn.close()
    print("Sample data added to database.")

if __name__ == '__main__':
    # Ensure the directories for videos exist if you are not creating dummy files
    # Example: os.makedirs(os.path.join(PROJECT_ROOT, 'src', 'recordings', 'camera1'), exist_ok=True)
    #          os.makedirs(os.path.join(PROJECT_ROOT, 'src', 'recordings', 'camera2'), exist_ok=True)
    init_db()
    add_sample_data()