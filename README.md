# SPL Alarm Standardisation P2 - Event Recording

![Python Version](https://img.shields.io/badge/python-3.9+-blue.svg)
![Framework](https://img.shields.io/badge/Framework-Flask-green.svg)
![Hardware](https://img.shields.io/badge/Hardware-NVIDIA%20Jetson-76B900)

This project is a real-time machine monitoring system developed for industrial environments. It captures video footage from machine-mounted cameras, saves critical clips when an error occurs, and provides a web-based dashboard for operators and engineers to review incidents. The system is designed to run on edge devices like the NVIDIA Jetson Orin Nano.

## Objective

The primary goal is to create a "black box" recorder for industrial machinery. When a machine fault is detected, the system automatically saves video from the moments leading up to and following the event, providing invaluable data for root cause analysis and process improvement.

## Key Features

-   **Event-Triggered Video Recording:** On a trigger (currently manual, planned for automation), the system saves the most recent video segments to an "incidents" folder, capturing pre- and post-error footage.
-   **Comprehensive Web Dashboard:** A Flask-powered web UI allows users to:
    -   View live feeds from multiple cameras.
    -   Review, sort, play back, and delete normal and incident recordings.
    -   Manage system settings and user access.
-   **Dynamic Camera Management:** Add, configure, and delete cameras directly from the web interface without restarting the server.
-   **Continuous Recording & Pruning:** Cameras record video in continuous segments, and the system automatically deletes the oldest files to manage disk space based on user-defined limits.
-   **Web-Optimized Video Output:** Captured videos are automatically processed with `ffmpeg` to ensure they are streamable and playable directly in a web browser without needing to be fully downloaded.
-   **Persistent Auto-Recording:** A designated camera can be configured to start recording automatically on system startup and continue recording even when users log out.
-   **Robust Data Logging:** Uses SQLite to store metadata for all video clips and incidents, enabling efficient retrieval and management.
-   **Standalone Video Manager:** A companion web utility (`video_manager_app.py`) for direct database administration, providing a centralized interface to review, manage, and clean up all video records from all cameras.
-   **Machine & Sensor Metadata:** Log qualitative information about each camera/sensor (e.g., location, type, notes) for better context.

## System Architecture

1.  **Video Capture:** The main Flask application (`main_app.py`) initializes and manages multiple `Camera` objects. Each camera can run a background thread to continuously capture video.
2.  **Continuous Loop Recording:** The capture thread saves video in short, timestamped segments to a `recordings` directory.
3.  **Video Post-Processing:** After each segment is saved, an `ffmpeg` process is called to re-encode the video to a web-compatible format (H.264/AAC) and optimize it for streaming (`-movflags +faststart`).
4.  **Event Trigger:** When an incident is triggered (currently via a button in the UI), the system identifies the most recent video segments.
5.  **Incident Archiving:** These recent segments are copied to a unique, timestamped folder within the `incidents` directory.
6.  **Database Logging:** Metadata for every normal and incident video (filename, timestamp, path) is logged in an SQLite database (`videos.db`).
7.  **Web Interface:** The Flask dashboard serves the live feeds, queries the database to display lists of recordings, and streams the video files for playback.

## Technology Stack

| Category      | Technology                                         |
| :------------ | :--------------------------------------------      |
| **Hardware**  | NVIDIA Jetson Orin Nano, Logitech C922 Camera      |
| **OS**        | Linux Ubuntu (Jetson OS)                           |
| **Backend**   | Python 3.9+                                        |
| **Framework** | Flask                                              |
| **Libraries** | OpenCV, SQLite3, Jinja2, Ultralytics (YOLOv8)      |
| **Video**     | `ffmpeg` for post-processing and optimization      |
| **Frontend**  | HTML, CSS, JavaScript                              |
| **Protocols** | `pymcprotocol` (Mitsubishi PLC), (Planned) OPC UA  |

## Setup and Deployment

This section covers how to set up and run the application, with a manual method for local development and a Docker method for robust deployment on the Jetson Nano.

### Manual Installation (for Local Development)

**Prerequisites:**
-   An NVIDIA Jetson device or a Linux/Windows machine.
-   Python 3.9+
-   `pip` and `venv`
-   **`ffmpeg`**: This is a critical dependency. It must be installed and accessible in the system's PATH.
    -   On Debian/Ubuntu: `sudo apt-get update && sudo apt-get install ffmpeg`

**Installation Steps:**
1.  **Clone the repository:** `git clone https://github.com/AllenJosee/SPL-Alarm-Standardisation-P2-Event-Recording`
2.  **Navigate into the directory:** `cd SPL-Alarm-Standardisation-P2-Event-Recording`
3.  **Create and activate a virtual environment:**
    -   `python3 -m venv venv`
    -   `source venv/bin/activate` (On Windows: `venv\Scripts\activate`)
4.  **Install dependencies:**
    *(A `requirements.txt` file is highly recommended. You can create one via `pip freeze > requirements.txt`.)*
    ```sh
    pip install Flask opencv-python numpy python-dateutil
    ```
5.  **Run the application:**
    ```sh
    python src/interface/main_app.py
    ```

### Finding Your IP Address to Access the Web UI

After running the application, you need your machine's local IP address to access the web interface from a browser on the same network. Here’s how to find it on different operating systems.

#### On Windows

1.  Open **Command Prompt** or **PowerShell**.
2.  Type the following command and press Enter:
    ```sh
    ipconfig
    ```
3.  Look for the `IPv4 Address` under your active network connection (e.g., 'Ethernet adapter' or 'Wireless LAN adapter Wi-Fi').

#### On Linux / Ubuntu (Jetson Nano)

1.  Open a **Terminal**.
2.  Type the following command and press Enter:
    ```sh
    ip addr
    ```
3.  Look for your network interface (e.g., `eth0` for a wired connection or `wlan0` for Wi-Fi). The IP address will be on the line that starts with `inet`.

---
The app runs on port `5001`. Once you have your IP address (e.g., `192.168.1.15`), you can access the application by navigating to `http://192.168.1.15:5001` in your web browser.

---

### Docker Deployment (Recommended for Jetson Nano)

This is the recommended method for deploying the application on a Jetson Nano. It ensures a consistent, isolated, and auto-restarting environment.

**1. Prerequisites**
-   **Docker:** Must be installed on your Jetson Nano.
-   **NVIDIA Container Toolkit:** Essential for allowing Docker containers to access the Jetson's GPU and devices like cameras. Follow the official NVIDIA documentation to install it.

**2. Create a `Dockerfile`**
Create a file named `Dockerfile` (no extension) in the root directory of your project with the following content. This version is specifically designed for the Jetson platform and your application's needs.

```Dockerfile
# Step 1: Use a base image compatible with NVIDIA Jetson
# This image is built for the ARM aarch64 architecture and has CUDA/GPU drivers.
FROM python:3.10-slim

# Step 2: Set the working directory inside the container
WORKDIR /app

# Step 3: Install system-level dependencies.
# - python3-pip is for installing Python packages.
# - ffmpeg is CRITICAL for video post-processing in main_app.py.
# - libgl1-mesa-glx and libglib2.0-0 are required by OpenCV.
RUN apt-get update && apt-get install -y \
    python3-pip \
    ffmpeg \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Step 4: Copy the requirements file and install Python dependencies.
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Step 5: Copy your entire project into the container's working directory.
COPY . .

# Step 6: Expose the port your Flask app runs on (5001).
EXPOSE 5001

# Step 7: Define the command to run your main application script.
CMD ["python3", "src/interface/main_app.py"]
```

**3. Build the Docker Image**
From the root directory of your project, run the following command to build the image:

```sh
docker build -t ads_autorecord:1.0 .
```

**4. Run the Docker Container**
Use the following command to start your application inside a container:

```sh
docker run -d \
  --restart=always \
  --name ads-autorecord \
  -p 5001:5001 \
  -v "$(pwd)":/app \
  --device=/dev/video0 \
  ads_autorecord:1.0
```

**Command Explanation:**
-   `docker run -d`: Runs the container in detached (background) mode.
-   `--restart=always`: Automatically restarts the container if it stops or on system reboot.
-   `--name ads-autorecord`: Assigns a memorable name to your container.
-   `-p 5001:5001`: Maps port 5001 on your Jetson (host) to port 5001 inside the container.
-   `-v "$(pwd)":/app`: **(Data Persistence)** Mounts your current project directory on the host into the `/app` directory inside the container. This ensures your `videos.db`, `cameras.json`, and all recorded videos are saved on your host machine and persist even if you delete the container.
-   `--device=/dev/video0`: Grants the container access to your camera device.
-   `ads_autorecord:1.0`: Specifies the image to run.

**5. Access and Manage the Application**
-   **Web UI:** Open a browser and go to `http://<your-jetson-ip>:5001/login`. For example: `http://192.168.137.12:5001/login`.
-   **Portainer (if used):** Access your Portainer instance at its configured address, e.g., `https://192.168.137.12:9443/`.

**6. Useful Docker Commands**

| Command                               | Description                                           |
| ------------------------------------- | ------------------------------------------------------|
| `docker ps`                           | See all running containers.                           |
| `docker ps -a`                        | See all containers, including stopped ones.           |
| `docker stop ads-autorecord`          | Stop the application container.                       |
| `docker start ads-autorecord`         | Start the container if it's stopped.                  |
| `docker restart ads-autorecord`       | Restart the container.                                |
| `docker logs ads-autorecord`          | View the application's output and logs.               |
| `docker logs -f ads-autorecord`       | Follow the logs in real-time.                         |
| `docker rm ads-autorecord`            | Permanently delete the container (must be stopped).   |
| `docker image list`                   | List all Docker images on your system.                |
| `docker image prune`                  | Remove old, unused image builds to save space.        |

---

## Project Internals

### Project Structure

This is the complete structure of the project, reflecting all major components.

```
.
├── ROI Detection/               # Standalone scripts for motion-based ROI event detection
├── data/                      # Raw data for model training (if any)
├── helmet_detection_runs/     # Output from YOLOv8 training/detection runs
├── logs/                      # Application log files
├── models/                    # Trained model files (e.g., best.pt for YOLO)
├── output/                    # General output directory for generated files/videos
├── src/                       # Main source code
│   ├── interface/
│   │   ├── main_app.py        # <<< MAIN APPLICATION ENTRY POINT
│   │   ├── video_manager_app.py # <<< DATABASE MANAGEMENT UTILITY
│   │   ├── static/            # CSS, JS, images, and HTML templates for Flask
│   │   ├── users.json         # User credentials for login
│   │   └── _archive/          # Deprecated and experimental server scripts
│   ├── recordings/          # Default location for continuous video segments
│   ├── incidents/           # Default location for archived incident clips
│   └── settings_cameraX.json # Per-camera settings files (created dynamically)
├── tests/                     # Unit and integration tests for the application
├── .gitignore                 # Specifies files for Git to ignore
├── cameras.json               # Main configuration file for all cameras
├── sensor_data.json           # Stores descriptive metadata for each sensor/camera
├── videos.db                  # SQLite database for normal video metadata
├── video_log.db               # (Likely) Another database, purpose to be confirmed
└── README.md                  # This file
```

### Companion Utility: Video Manager

The file `src/interface/video_manager_app.py` is a separate, lightweight Flask application that acts as a database administration tool. It provides a simple web interface to directly view, play, download, and delete video records from the `videos.db` database.

**Purpose:**
-   To manage the video database without needing the main camera capture application (`main_app.py`) to be running.
-   Useful for maintenance, data cleanup, and direct review of all recorded footage.

**How to Run:**
```sh
# Ensure you are in your activated virtual environment
python src/interface/video_manager_app.py
```
This utility runs on port **5000** by default.

### Project Evolution & Archive Details

The `src/interface/_archive/` directory contains previous versions and experimental scripts that were developed during the evolution of the main application. These files are **not meant to be run** and are kept for historical reference.

#### Server Iterations
-   **`server.py`**: The initial proof-of-concept. A basic Flask server that initialized all cameras on startup (`cv2.VideoCapture` in `__init__`), leading to slow launch times with multiple cameras.
-   **`server2.py`**: The second iteration, which introduced "lazy initialization" of cameras. It used a `_get_or_init_capture()` method to only activate a camera's hardware when a user accessed its feed, resulting in a much faster initial load time.
-   **`server2_db.py` / `server2_db_ubuntu.py`**: A major upgrade to `server2.py` that added a robust **SQLite database** for logging video metadata. This replaced simple file-based listing and enabled features like sorting and persistent records. The `_ubuntu` version contained OS-specific fixes for deployment on Linux.
-   **`server2_picam_PLC.py`**: A hardware-specific version adapted to run on a **Raspberry Pi 4B**. It used the `picamera2` library and included experimental **PLC integration** with `pymcprotocol` for communicating with Mitsubishi PLCs.
-   **`server3_auto_record.py`**: Introduced the crucial feature of **persistent automatic recording**. It implemented more advanced threading with `Lock` and `Event` objects to allow a designated camera to start recording on launch and continue even when a user logs out, both of which functions are now combined and implemented in the **`main_app.py`**
-   **`server4_obj_detect.py`**: A prototype for AI-based computer vision. It integrated a **YOLOv8** model (`ultralytics` library) to perform real-time object detection (e.g., helmet vs. head) on the live video feed, serving as a testbed for using AI as an incident trigger.

#### Running Archived Scripts (For Advanced Users / Experimental Use Only)

> **Warning:** This is not recommended for production or regular use. These scripts are deprecated, may contain bugs, and lack the stability and features of `main_app.py`. Always back up your `videos.db` and other data before proceeding.

If you wish to run an archived script for testing or educational purposes:
1.  **Move the Script:** Copy or move the desired Python file (e.g., `server2.py`) from the `src/interface/_archive/` directory to its parent directory, `src/interface/`.
2.  **Check Dependencies:** The script may require libraries not installed for `main_app.py` (e.g., `server2_picam_PLC.py` requires `picamera2` and `pymcprotocol`). You may need to install them manually with `pip`.
3.  **Be Aware of Limitations:**
    *   **File Paths:** Older scripts may have hardcoded paths that do not work correctly without modification.
    *   **Database:** Scripts older than `server2_db.py` do not interact with the SQLite database and will not log video metadata.
    *   **Functionality:** Features like persistent auto-recording or object detection will only be present in the specific scripts where they were developed.

#### Utility and Interface Scripts
-   **`plc_interface.py` & `opcua_client.py`**: Prototypes for industrial communication, demonstrating proof-of-concept for connecting to PLCs via **OPC UA** to enable automated error triggering.
-   **`db_utils.py` & `check_gpu.py`**: Simple helper scripts for database operations and verifying GPU access on the Jetson Nano.

### ROI Detection (Alternative Trigger Method)

The `ROI Detection/` folder contains standalone Python scripts for an optional, alternative incident trigger method. This method uses traditional computer vision techniques instead of a deep learning model. **Note: This functionality is not yet integrated into the main Flask application.**

#### How it Works

The scripts use **background subtraction** to detect a moving object (like a machine arm) within a large `Detection Zone`. The algorithm then tracks the center of this object. If the object's center enters one of two smaller, user-defined trigger zones (`Top Zone` for 'Open' events, `Bottom Zone` for 'Close' events), it registers a count.

This method is lightweight, runs efficiently on a CPU, and is ideal for monitoring simple, repetitive motions in a static scene without needing a trained AI model.

#### Key Scripts & Tuning

-   **`main.py`**: Performs real-time detection on a video file. Use this for testing and tuning parameters.
-   **`test_save-vid.py`**: Same as `main.py`, but also saves the processed video to an `output.mp4` file.

To use these scripts, you must edit the file and adjust the key parameters at the top. This includes:
-   The `VIDEO_PATH` to your source video.
-   The coordinates for the different **ROIs** (`DETECTION_ROI`, `TOP_ZONE_ROI`, etc.).
-   Motion sensitivity thresholds like `BG_VAR_THRESHOLD` and `MIN_CONTOUR_AREA_ARM`.

#### How to Use

1.  Navigate to the `ROI Detection/` directory.
2.  Edit `main.py` or `test_save-vid.py` to set the video path and tune the ROI parameters.
3.  Run the script from your terminal: `python main.py`

---

## Deployment Notes

-   **Machine Selection:** The system is ideal for machines with high error frequency or criticality (e.g., Machine CF 653).
-   **Camera Placement:** Position the camera to cover the most critical, error-prone zones of the machine. Ensure adequate lighting and use a stable, vibration-dampened mount. The view should be clear of obstructions from machine parts or operator movement.

## Future Works

-   **Implement Advanced Automated Triggers:** The current manual "Simulate Incident" button can be replaced with a robust, automated system offering multiple trigger methods:
    -   **Industrial Protocol Integration:** Fully integrate with PLCs/SCADA systems via **OPC UA, Modbus, or MQTT** to trigger recording based on machine fault codes.
    -   **AI-Based Visual Detection:** Integrate the prototyped **YOLOv8** model (from `server4_obj_detect.py`) to detect visual anomalies in real-time (e.g., incorrect part placement, missing safety gear) and automatically trigger an incident.
    -   **Motion-Based ROI Detection:** Add the logic from the `ROI Detection/` module as a selectable, CPU-friendly trigger option for monitoring simple, repetitive mechanical movements on a static background.

-   **Advanced User Roles:** Expand the user authentication system to include distinct roles (e.g., Admin, Operator, Maintenance) with different permissions.

-   **NVR Integration:** Integrate with open-source NVR platforms like ZoneMinder or Shinobi for more advanced surveillance and recording management features.