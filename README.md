# SPL Alarm Standardisation P2 - Event Recording

![Python Version](https://img.shields.io/badge/python-3.9+-blue.svg)
![Framework](https://img.shields.io/badge/Framework-Flask-green.svg)
![Hardware](https://img.shields.io/badge/Hardware-NVIDIA%20Jetson-76B900)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

This project is a real-time machine monitoring system developed for industrial environments. It captures video footage from machine-mounted cameras, saves critical clips when an error occurs, and provides a web-based dashboard for operators and engineers to review incidents. The system is designed to run on edge devices like the NVIDIA Jetson Nano.

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
-   **Persistent Auto-Recording:** A designated camera can be configured to start recording automatically on system startup.
-   **Robust Data Logging:** Uses SQLite to store metadata for all video clips and incidents, enabling efficient retrieval and management.
-   **Machine & Sensor Metadata:** Log qualitative information about each camera/sensor (e.g., location, type, notes) for better context.

## System Architecture

1.  **Video Capture:** The Flask application initializes and manages multiple `Camera` objects. Each camera runs a background thread to continuously capture video from its source.
2.  **Continuous Loop Recording:** The capture thread saves video in short, timestamped segments (e.g., 5-10 seconds long) to a `recordings` directory.
3.  **Video Post-Processing:** After each segment is saved, an `ffmpeg` process is called to re-encode the video to a web-compatible format (H.264/AAC) and optimize it for streaming (`-movflags +faststart`).
4.  **Event Trigger:** When an incident is triggered (currently via a button in the UI), the system identifies the most recent video segments.
5.  **Incident Archiving:** These recent segments are copied to a unique, timestamped folder within the `incidents` directory.
6.  **Database Logging:** Metadata for every normal and incident video (filename, timestamp, path) is logged in an SQLite database.
7.  **Web Interface:** The Flask dashboard serves the live feeds, queries the database to display lists of recordings, and streams the video files for playback.

## Technology Stack

| Category      | Technology                                         |
| :------------ | :--------------------------------------------      |
| **Hardware**  | NVIDIA Jetson Orin Nano, Logitech C922 Camera      |
| **OS**        | Linux Ubuntu (Jetson Nano OS)                      |
| **Backend**   | Python 3.9+                                        |
| **Framework** | Flask                                              |
| **Libraries** | OpenCV, SQLite3, Jinja2                            |
| **Video**     | `ffmpeg` for post-processing and optimization      |
| **Frontend**  | HTML, CSS, JavaScript                              |
| **Protocols** | (Planned) OPC UA, Modbus, MQTT                     |

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
2.  **Navigate into the directory:** `cd <repository-folder>`
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
    The app runs on port `5001`. Access it at `http://<your-ip>:5001`.

---

### Docker Deployment (Recommended for Jetson Nano)

This is the recommended method for deploying the application on a Jetson Nano. It ensures a consistent, isolated, and auto-restarting environment.

**1. Prerequisites**
-   **Docker:** Must be installed on your Jetson Nano.
-   **NVIDIA Container Toolkit:** Essential for allowing Docker containers to access the Jetson's GPU and devices like cameras. Follow the official NVIDIA documentation to install it.

**2. Create a `Dockerfile`**
Create a file named `Dockerfile` (no extension) in the root directory of your project with the following content:

```Dockerfile
# Use a base image compatible with Jetson Nano
FROM nvcr.io/nvidia/l4t-base:r32.7.1

# Set the working directory inside the container
WORKDIR /app

# Install system dependencies, including python, pip, and ffmpeg
RUN apt-get update && apt-get install -y \
    python3-pip \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file and install Python packages
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Expose the port the Flask app runs on
EXPOSE 5001

# The command to run when the container starts
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

This is the actual structure of the Flask application.

```
.
├── src/
│   ├── interface/
│   │   ├── main_app.py        # <<< MAIN APPLICATION ENTRY POINT
│   │   ├── static/            # CSS, JS, images, and HTML templates
│   │   ├── users.json         # User credentials for login
│   │   └── _archive/          # Deprecated and experimental server scripts
│   ├── recordings/          # Default location for continuous video segments
│   ├── incidents/           # Default location for archived incident clips
│   └── settings_cameraX.json # Per-camera settings files
├── cameras.json             # Main configuration file for all cameras
├── sensor_data.json         # Stores descriptive metadata for each sensor/camera
├── videos.db                # SQLite database for video and incident metadata
└── README.md                # Project description and setup instructions
```

### Project Evolution & Archive Details

The `src/interface/_archive/` directory contains previous versions and experimental scripts that were developed during the evolution of the main application. These files are **not meant to be run** and are kept for historical reference. **The only script that should be run is `main_app.py`**.

#### Server Iterations
-   **`server.py`**: The initial proof-of-concept; a basic Flask server for streaming a single video source. Starts up slow due to initialising all cameras at once. 
-   **`server2.py` / `server2_db.py` / `server2_db_ubuntu.py`**: The second iteration, now wiht lazy initialisation method where the camera objects will only be initialised when needed, and introduced SQLite for logging video metadata and included OS-specific bug fixes for deployment on Ubuntu/Jetson.
-   **`server2_picam_new.py`**: A hardware-specific version adapted to use a Raspberry Pi's `picamera` library, showing an early exploration of different edge devices.
-   **`server3_auto_record.py`**: Introduced the crucial feature of automatic, continuous recording in segments, the core of which was merged into `main_app.py`.
-   **`server4_obj_detect.py`**: A prototype for AI-based computer vision using YOLOv8 models (e.g., helmet detection), serving as a testbed for ML features. The detection algorithm can run over the live feed to output the helmet detection in real-time. can be implemented as the trigger condition for the incidents. 

#### Utility and Interface Scripts
-   **`db_utils.py`**: A module to separate database functions (create, insert, query) from the main application logic.
-   **`plc_interface.py` & `opcua_client.py`**: Critically important prototypes for industrial communication. They demonstrate proof-of-concept for connecting to PLCs via **OPC UA** to enable automated error triggering.
-   **`check_gpu.py`**: A simple hardware check script to verify GPU access on the Jetson Nano.
-   **`setup.py`**: A standard Python script to make the project installable as a package.

---

## Deployment Notes

-   **Machine Selection:** The system is ideal for machines with high error frequency or criticality (e.g., Machine CF 653).
-   **Camera Placement:** Position the camera to cover the most critical, error-prone zones of the machine. Ensure adequate lighting and use a stable, vibration-dampened mount. The view should be clear of obstructions from machine parts or operator movement.

## Future Works

-   **Automated Error Triggering:** Integrate with PLCs/SCADA systems via **OPC UA, Modbus, or MQTT** to automatically trigger incident recording when a machine fault code is generated.
-   **AI-Based Anomaly Detection:** Implement a machine learning model (e.g., using YOLO or TensorFlow) to analyze the video feed in real-time and detect visual anomalies.
-   **Advanced User Roles:** Expand the user authentication system to include distinct roles (e.g., Admin, Operator, Maintenance) with different permissions.
-   **NVR Integration:** Integrate with open-source NVR platforms like ZoneMinder or Shinobi for more advanced surveillance and recording management features.
-   **Multi-Camera Synchronization:** For complex machines, implement multi-camera setups with synchronized recording and playback.