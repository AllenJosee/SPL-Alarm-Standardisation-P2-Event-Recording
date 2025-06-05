#to retrieve and manage the database directly
#Playback, Delete and Download
#Delete from both the database and the disk

#SQLite DB Browser can be used as alternative. 

from flask import Flask, render_template, jsonify, send_from_directory, request, g
import sqlite3
import os
import logging
import datetime
from dateutil import parser

# --- Path Setup ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT_DIR = os.path.dirname(SRC_DIR)

# --- Database Configuration ---
DATABASE_NAME = 'videos.db'
TABLE_NAME = 'video_metadata'
INCIDENT_TABLE_NAME = 'incident_video_metadata' 

DATABASE_PATH = os.path.join(PROJECT_ROOT_DIR, DATABASE_NAME)

TEMPLATE_FOLDER_PATH = os.path.join(SCRIPT_DIR, 'static', 'templates')
STATIC_FOLDER_PATH = os.path.join(SCRIPT_DIR, 'static')

ALLOWED_SORT_COLUMNS_RECORDINGS = { 
    'id': 'id',
    'camera_id': 'camera_id',
    'filename': 'filename',
    'timestamp': 'timestamp'
}

ALLOWED_SORT_COLUMNS_INCIDENTS = { 
    'id': 'id',
    'camera_id': 'camera_id',
    'filename': 'original_video_filename', 
    'folder': 'incident_folder_name',
    'timestamp': 'incident_trigger_timestamp' 
}


app = Flask(__name__, template_folder=TEMPLATE_FOLDER_PATH, static_folder=STATIC_FOLDER_PATH)

# Configure logging
logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)


def get_db():
    db = getattr(g, '_database', None) # Get the connection stored on g
    if db is None:
        db = g._database = sqlite3.connect(DATABASE_PATH)
        db.row_factory = sqlite3.Row # Access columns by name
    return db

def format_display_timestamp(iso_timestamp_str_utc): # Input is assumed to be UTC ISO string
    """Helper to parse UTC ISO timestamp and format for display in Singapore Time (SGT, UTC+8)."""
    if not iso_timestamp_str_utc:
        return "N/A"
    try:
        # Parse the ISO string. parser.isoparse should correctly handle 'Z' or '+00:00' as UTC.
        dt_object_utc = parser.isoparse(iso_timestamp_str_utc)

        # Ensure it's timezone-aware and set to UTC if it's naive (though isoparse usually handles Z)
        if dt_object_utc.tzinfo is None or dt_object_utc.tzinfo.utcoffset(dt_object_utc) is None:
            dt_object_utc = dt_object_utc.replace(tzinfo=datetime.timezone.utc)
        else:
            # If it already has timezone info, convert it to UTC first to be sure
            dt_object_utc = dt_object_utc.astimezone(datetime.timezone.utc)

        # Define Singapore Timezone (SGT = UTC+8)
        sgt_timezone = datetime.timezone(datetime.timedelta(hours=8))

        # Convert UTC datetime object to SGT
        dt_object_sgt = dt_object_utc.astimezone(sgt_timezone)

        # Format for display in SGT
        return dt_object_sgt.strftime("%Y-%m-%d %H:%M:%S SGT") # Example: 2025-05-26 13:25:15 SGT
        # Or a more friendly format:
        # return dt_object_sgt.strftime("%Y-%m-%d %I:%M:%S %p SGT") # Example: 2025-05-26 01:25:15 PM SGT

    except (ValueError, TypeError) as e:
        app.logger.warning(f"Could not parse/convert timestamp: '{iso_timestamp_str_utc}'. Error: {e}")
        return iso_timestamp_str_utc # Return original if parsing/conversion fails
    except Exception as e_gen: # Catch any other unexpected errors
        app.logger.error(f"Unexpected error formatting timestamp '{iso_timestamp_str_utc}': {e_gen}")
        return iso_timestamp_str_utc

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None) # Get the connection stored on g
    if db is not None:
        db.close()

@app.route('/') 
def show_recordings_page():
    conn = get_db()
    cursor = conn.cursor()

    # Initialize data to be passed to template
    unique_camera_ids = []
    recordings_data_processed = [] # This will hold the final list for the template
    query_params = [] # Initialize query_params

    # 1. Fetch Unique Camera IDs for Filter Dropdown
    try:
        cursor.execute(f"SELECT DISTINCT camera_id FROM {TABLE_NAME} ORDER BY camera_id ASC")
        unique_camera_ids_tuples = cursor.fetchall()
        unique_camera_ids = [item['camera_id'] for item in unique_camera_ids_tuples] 
    except sqlite3.Error as e:
        app.logger.error(f"Database error fetching unique camera IDs for recordings: {e}")
        # unique_camera_ids remains []

    # 2. Get Request Arguments for Sorting and Filtering
    sort_by_param = request.args.get('sort_by', 'timestamp')
    sort_order_param = request.args.get('sort_order', 'desc')
    filter_camera_id_param = request.args.get('filter_camera_id', '')

    # 3. Build SQL Query
    db_column_to_sort_by = ALLOWED_SORT_COLUMNS_RECORDINGS.get(sort_by_param, 'timestamp')
    sql_sort_order = 'DESC' if sort_order_param.lower() == 'desc' else 'ASC'
    
    base_query = f"SELECT id, camera_id, filename, timestamp, path FROM {TABLE_NAME}"
    where_clauses = []
    # query_params is already initialized

    if filter_camera_id_param and filter_camera_id_param != 'all':
        where_clauses.append("camera_id = ?")
        query_params.append(filter_camera_id_param) # Populate query_params
    
    sql_where_clause = ""
    if where_clauses:
        sql_where_clause = " WHERE " + " AND ".join(where_clauses)
    
    # Define final_query here, once, before it's used for execution
    final_query = f"{base_query}{sql_where_clause} ORDER BY {db_column_to_sort_by} {sql_sort_order}, id {sql_sort_order}" # Added secondary sort by id

    app.logger.info(f"DEBUG: Attempting to execute recordings query: {final_query} with params: {query_params}") # Add "DEBUG:" prefix
    
    # 4. Execute Main Query and Process Data
    try:
        cursor.execute(final_query, tuple(query_params)) # Execute the built query
        recordings_raw_data = cursor.fetchall()
        
        for row in recordings_raw_data: # Process the raw data
            recordings_data_processed.append({
                "id": row["id"],
                "camera_id": row["camera_id"],
                "filename": row["filename"],
                "timestamp": row["timestamp"], # ISO string from DB
                "display_timestamp": format_display_timestamp(row["timestamp"]), # Formatted
                "path": row["path"]
            })
    except sqlite3.Error as e:
        app.logger.error(f"Database error fetching recordings from {TABLE_NAME}: {e}")
        # recordings_data_processed remains []
    
    # 5. Render Template
    return render_template('recordings_view.html', 
                           recordings=recordings_data_processed, # Pass the processed list
                           unique_camera_ids=unique_camera_ids,
                           current_filter_camera_id=filter_camera_id_param,
                           current_sort_by=sort_by_param,
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



@app.route('/incidents')
def show_incidents_page(): # Removed duplicate @app.route('/incidents')
    conn = get_db()
    cursor = conn.cursor()

    # Initialize data
    unique_camera_ids_incident = []
    incidents_data_processed = [] # For final processed data
    query_params_incident = []    # Initialize

    # 1. Fetch Unique Camera IDs for Incident Filter
    try:
        cursor.execute(f"SELECT DISTINCT camera_id FROM {INCIDENT_TABLE_NAME} ORDER BY camera_id ASC")
        unique_camera_ids_tuples_incident = cursor.fetchall()
        unique_camera_ids_incident = [item['camera_id'] for item in unique_camera_ids_tuples_incident]
    except sqlite3.Error as e:
        app.logger.error(f"Database error fetching unique camera IDs for incidents: {e}")

    # 2. Get Request Arguments
    sort_by_param = request.args.get('sort_by', 'timestamp')
    sort_order_param = request.args.get('sort_order', 'desc')
    filter_camera_id_param = request.args.get('filter_camera_id', '')
    
    # 3. Build SQL Query for Incidents
    db_column_to_sort_by = ALLOWED_SORT_COLUMNS_INCIDENTS.get(sort_by_param, 'incident_trigger_timestamp')
    sql_sort_order = 'DESC' if sort_order_param.lower() == 'desc' else 'ASC'

    base_query_incident = f"SELECT id, camera_id, original_video_filename, incident_trigger_timestamp, incident_folder_name, path FROM {INCIDENT_TABLE_NAME}"
    where_clauses_incident = []
    # query_params_incident is already initialized

    if filter_camera_id_param and filter_camera_id_param != 'all':
        where_clauses_incident.append("camera_id = ?")
        query_params_incident.append(filter_camera_id_param) # Populate query_params_incident
    
    sql_where_clause_incident = ""
    if where_clauses_incident:
        sql_where_clause_incident = " WHERE " + " AND ".join(where_clauses_incident)

    final_query_incident = f"{base_query_incident}{sql_where_clause_incident} ORDER BY {db_column_to_sort_by} {sql_sort_order}, id {sql_sort_order}"
    
    app.logger.info(f"Executing incidents query: {final_query_incident} with params: {query_params_incident}")
    
    # 4. Execute Main Query and Process Data
    try:
        cursor.execute(final_query_incident, tuple(query_params_incident))
        incidents_raw_data = cursor.fetchall()
        
        for row in incidents_raw_data: # Process raw data
            incidents_data_processed.append({
                "id": row["id"],
                "camera_id": row["camera_id"],
                "original_video_filename": row["original_video_filename"],
                "incident_folder_name": row["incident_folder_name"],
                "incident_trigger_timestamp": row["incident_trigger_timestamp"],
                "display_timestamp": format_display_timestamp(row["incident_trigger_timestamp"]),
                "path": row["path"]
            })
    except sqlite3.Error as e:
        app.logger.error(f"Database error fetching incidents from {INCIDENT_TABLE_NAME}: {e}")
        # incidents_data_processed remains []
    
    # 5. Render Template
    return render_template('incidents_view.html',
                           incidents=incidents_data_processed, # Pass processed list
                           unique_camera_ids_incident=unique_camera_ids_incident,
                           current_filter_camera_id=filter_camera_id_param,
                           current_sort_by=sort_by_param,
                           current_sort_order=sort_order_param)

@app.route('/play_incident_video/<int:incident_id>')
def play_incident_video_file(incident_id):
    conn = get_db()
    cursor = conn.cursor()
    query = f"SELECT path FROM {INCIDENT_TABLE_NAME} WHERE id = ?"
    cursor.execute(query, (incident_id,))
    incident = cursor.fetchone()
    if incident:
        db_relative_path = incident['path']
        video_directory_absolute = os.path.join(PROJECT_ROOT_DIR, os.path.dirname(db_relative_path))
        video_filename = os.path.basename(db_relative_path)
        
        app.logger.info(f"Playing Incident: Dir='{video_directory_absolute}', File='{video_filename}'")
        if not os.path.exists(os.path.join(video_directory_absolute, video_filename)):
            app.logger.error(f"Incident file not found on disk: {os.path.join(video_directory_absolute, video_filename)}")
            return "Incident video file not found on server", 404
        return send_from_directory(video_directory_absolute, video_filename)
    app.logger.warning(f"Incident ID {incident_id} not found in {INCIDENT_TABLE_NAME} for playback.")
    return f"Incident video not found in {INCIDENT_TABLE_NAME}", 404

@app.route('/download_incident_video/<int:incident_id>')
def download_incident_video_file(incident_id):
    conn = get_db()
    cursor = conn.cursor()
    #original_video_filename for the download prompt
    query = f"SELECT path, original_video_filename FROM {INCIDENT_TABLE_NAME} WHERE id = ?"
    cursor.execute(query, (incident_id,))
    incident = cursor.fetchone()
    if incident:
        db_relative_path = incident['path']
        download_as_filename = incident['original_video_filename'] # Use original filename for download

        video_directory_absolute = os.path.join(PROJECT_ROOT_DIR, os.path.dirname(db_relative_path))
        actual_filename_on_disk = os.path.basename(db_relative_path) 
        
        app.logger.info(f"Downloading Incident: Dir='{video_directory_absolute}', File='{actual_filename_on_disk}', As='{download_as_filename}'")
        if not os.path.exists(os.path.join(video_directory_absolute, actual_filename_on_disk)):
            app.logger.error(f"Incident file not found on disk for download: {os.path.join(video_directory_absolute, actual_filename_on_disk)}")
            return "Incident video file not found on server", 404
        return send_from_directory(
            video_directory_absolute,
            actual_filename_on_disk,
            as_attachment=True,
            download_name=download_as_filename
        )
    app.logger.warning(f"Incident ID {incident_id} not found in {INCIDENT_TABLE_NAME} for download.")
    return f"Incident video not found in {INCIDENT_TABLE_NAME}", 404

@app.route('/delete_incident_video/<int:incident_id>', methods=['POST'])
def delete_incident_video_entry(incident_id):
    conn = get_db()
    cursor = conn.cursor()
    query_select = f"SELECT path FROM {INCIDENT_TABLE_NAME} WHERE id = ?"
    cursor.execute(query_select, (incident_id,))
    incident = cursor.fetchone()

    if incident:
        try:
            db_relative_path = incident['path']
            absolute_file_path_on_disk = os.path.join(PROJECT_ROOT_DIR, db_relative_path)

            file_deleted_from_disk = False
            if os.path.exists(absolute_file_path_on_disk):
                os.remove(absolute_file_path_on_disk)
                app.logger.info(f"Deleted incident file from disk: {absolute_file_path_on_disk}")
                file_deleted_from_disk = True
            else:
                app.logger.warning(f"Incident file not found on disk for deletion: {absolute_file_path_on_disk}")

            query_delete = f"DELETE FROM {INCIDENT_TABLE_NAME} WHERE id = ?"
            cursor.execute(query_delete, (incident_id,))
            conn.commit()
            app.logger.info(f"Deleted incident ID {incident_id} from {INCIDENT_TABLE_NAME}.")
            return jsonify(success=True, message=f"Incident video ID {incident_id} deleted." + (" File also removed." if file_deleted_from_disk else " File not found on disk."))
        except Exception as e:
            conn.rollback()
            app.logger.error(f"Error deleting incident {incident_id} from {INCIDENT_TABLE_NAME}: {e}")
            return jsonify(success=False, message=str(e)), 500
    app.logger.warning(f"Incident ID {incident_id} not found in {INCIDENT_TABLE_NAME} for deletion.")
    return jsonify(success=False, message=f"Incident video not found in {INCIDENT_TABLE_NAME}."), 404


if __name__ == '__main__':
    app.logger.info(f"--- Video Manager App Starting ---")
    app.logger.info(f"Project Root: {PROJECT_ROOT_DIR}")
    app.logger.info(f"Connecting to Database: {DATABASE_PATH}")
    app.logger.info(f"Using Recordings Table: {TABLE_NAME}")
    app.logger.info(f"Using Incidents Table: {INCIDENT_TABLE_NAME}") # Log the new table name
    app.logger.info(f"Template Folder: {app.template_folder}")
    app.logger.info(f"Static Folder: {app.static_folder}")

    if not os.path.exists(DATABASE_PATH):
        app.logger.error(f"CRITICAL: DATABASE '{DATABASE_NAME}' NOT FOUND AT: {DATABASE_PATH}")
        app.logger.error("Please ensure the database exists and is correctly pathed. You might need to run your main app's init_db() or a setup script.")
    
    app.run(debug=True, host='0.0.0.0', port=5000)

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
    
    app.run(debug=True, host='0.0.0.0', port=5000)