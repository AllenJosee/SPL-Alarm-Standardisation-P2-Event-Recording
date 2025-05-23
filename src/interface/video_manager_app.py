from flask import Flask, render_template, jsonify, send_from_directory, request, g
import sqlite3
import os
import logging

# --- Path Setup ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT_DIR = os.path.dirname(SRC_DIR)

# --- Database Configuration ---
# Use the database name and table name from your existing setup
DATABASE_NAME = 'videos.db'
TABLE_NAME = 'video_metadata'
DATABASE_PATH = os.path.join(PROJECT_ROOT_DIR, DATABASE_NAME)

# Template and static folder paths relative to SCRIPT_DIR
TEMPLATE_FOLDER_PATH = os.path.join(SCRIPT_DIR, 'static', 'templates')
STATIC_FOLDER_PATH = os.path.join(SCRIPT_DIR, 'static')

app = Flask(__name__, template_folder=TEMPLATE_FOLDER_PATH, static_folder=STATIC_FOLDER_PATH)

# Configure logging
logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)


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

@app.route('/') # Assuming this is the route for recordings_view.html
def show_recordings_page():
    conn = get_db()
    cursor = conn.cursor()

    # Get sort parameters from URL query
    # Default sort: by timestamp, descending (most recent first)
    sort_by_param = request.args.get('sort_by', 'timestamp')
    sort_order_param = request.args.get('sort_order', 'desc')

    # Whitelist allowed sort columns (keys are URL params, values are actual DB column names)
    allowed_sort_columns = {
        'id': 'id',
        'camera_id': 'camera_id',
        'filename': 'filename',
        'timestamp': 'timestamp' # 'timestamp' column stores your date/time string
    }
    
    # Validate sort_by_param, default to 'timestamp' if invalid
    db_column_to_sort_by = allowed_sort_columns.get(sort_by_param, 'timestamp')

    # Validate sort_order_param, default to 'desc' if invalid
    if sort_order_param.lower() not in ['asc', 'desc']:
        sort_order_param = 'desc'
    
    sql_sort_order = sort_order_param.upper()

    # Construct the query with ORDER BY
    # Ensure your TABLE_NAME and column names (id, camera_id, filename, timestamp, path) are correct
    query = f"""
        SELECT id, camera_id, filename, timestamp, path 
        FROM {TABLE_NAME} 
        ORDER BY {db_column_to_sort_by} {sql_sort_order}
    """
    # For a secondary sort (e.g., if timestamps are identical, sort by ID):
    # query = f"""
    #     SELECT id, camera_id, filename, timestamp, path
    #     FROM {TABLE_NAME}
    #     ORDER BY {db_column_to_sort_by} {sql_sort_order}, id {sql_sort_order}
    # """

    try:
        cursor.execute(query)
        recordings_data = cursor.fetchall()
    except sqlite3.Error as e:
        app.logger.error(f"Database error fetching recordings from {TABLE_NAME}: {e}")
        recordings_data = []
    
    return render_template('recordings_view.html', 
                           recordings=recordings_data,
                           current_sort_by=sort_by_param,  # Pass the param name used in URL
                           current_sort_order=sort_order_param)


@app.route('/play_video/<int:recording_id>')
def play_video_file(recording_id):
    conn = get_db()
    cursor = conn.cursor()
    query = f"SELECT path FROM {TABLE_NAME} WHERE id = ?"
    cursor.execute(query, (recording_id,))
    recording = cursor.fetchone()
    if recording:
        db_relative_path = recording['path'] # 'path' is the column name from your table
        video_directory_absolute = os.path.join(PROJECT_ROOT_DIR, os.path.dirname(db_relative_path))
        video_filename = os.path.basename(db_relative_path)
        
        app.logger.info(f"Attempting to play: Dir='{video_directory_absolute}', File='{video_filename}'")
        if not os.path.exists(os.path.join(video_directory_absolute, video_filename)):
            app.logger.error(f"Video file not found on disk: {os.path.join(video_directory_absolute, video_filename)}")
            return "Video file not found on server", 404
        return send_from_directory(video_directory_absolute, video_filename)
    app.logger.warning(f"Recording ID {recording_id} not found in {TABLE_NAME} for playback.")
    return f"Video recording not found in {TABLE_NAME}", 404

@app.route('/download_video/<int:recording_id>')
def download_video_file(recording_id):
    conn = get_db()
    cursor = conn.cursor()
    query = f"SELECT path, filename FROM {TABLE_NAME} WHERE id = ?" # 'filename' is from your table
    cursor.execute(query, (recording_id,))
    recording = cursor.fetchone()
    if recording:
        db_relative_path = recording['path']
        download_as_filename = recording['filename']

        video_directory_absolute = os.path.join(PROJECT_ROOT_DIR, os.path.dirname(db_relative_path))
        actual_filename_on_disk = os.path.basename(db_relative_path)
        
        app.logger.info(f"Attempting to download: Dir='{video_directory_absolute}', File='{actual_filename_on_disk}', As='{download_as_filename}'")
        if not os.path.exists(os.path.join(video_directory_absolute, actual_filename_on_disk)):
            app.logger.error(f"Video file not found on disk for download: {os.path.join(video_directory_absolute, actual_filename_on_disk)}")
            return "Video file not found on server", 404
        return send_from_directory(
            video_directory_absolute,
            actual_filename_on_disk,
            as_attachment=True,
            download_name=download_as_filename
        )
    app.logger.warning(f"Recording ID {recording_id} not found in {TABLE_NAME} for download.")
    return f"Video recording not found in {TABLE_NAME}", 404

@app.route('/delete_video/<int:recording_id>', methods=['POST'])
def delete_video_entry(recording_id):
    conn = get_db()
    cursor = conn.cursor()
    query_select = f"SELECT path FROM {TABLE_NAME} WHERE id = ?"
    cursor.execute(query_select, (recording_id,))
    recording = cursor.fetchone()

    if recording:
        try:
            db_relative_path = recording['path']
            absolute_file_path_on_disk = os.path.join(PROJECT_ROOT_DIR, db_relative_path)

            file_deleted_from_disk = False
            if os.path.exists(absolute_file_path_on_disk):
                os.remove(absolute_file_path_on_disk)
                app.logger.info(f"Deleted file from disk: {absolute_file_path_on_disk}")
                file_deleted_from_disk = True
            else:
                app.logger.warning(f"File not found on disk for deletion: {absolute_file_path_on_disk}")

            query_delete = f"DELETE FROM {TABLE_NAME} WHERE id = ?"
            cursor.execute(query_delete, (recording_id,))
            conn.commit()
            app.logger.info(f"Deleted recording ID {recording_id} from {TABLE_NAME}.")
            return jsonify(success=True, message=f"Recording ID {recording_id} deleted." + (" File also removed." if file_deleted_from_disk else " File not found on disk."))
        except Exception as e:
            conn.rollback()
            app.logger.error(f"Error deleting recording {recording_id} from {TABLE_NAME}: {e}")
            return jsonify(success=False, message=str(e)), 500
    app.logger.warning(f"Recording ID {recording_id} not found in {TABLE_NAME} for deletion.")
    return jsonify(success=False, message=f"Recording not found in {TABLE_NAME}."), 404

if __name__ == '__main__':
    app.logger.info(f"--- Video Manager App Starting ---")
    app.logger.info(f"Project Root: {PROJECT_ROOT_DIR}")
    app.logger.info(f"Connecting to Database: {DATABASE_PATH}")
    app.logger.info(f"Using Table: {TABLE_NAME}")
    app.logger.info(f"Template Folder: {app.template_folder}")
    app.logger.info(f"Static Folder: {app.static_folder}")

    if not os.path.exists(DATABASE_PATH):
        app.logger.error(f"CRITICAL: DATABASE '{DATABASE_NAME}' NOT FOUND AT: {DATABASE_PATH}")
        app.logger.error("Please ensure the database exists and is correctly pathed.")
    
    app.run(debug=True, host='0.0.0.0', port=5001)