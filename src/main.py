# main.py

from processing.video_recorder import record_video
from datetime import datetime
import os

# %% Directoy allocations
log_file = "/home/pi/dashcam/video_log.txt"
vid_dir = "C:/Users/SPLC4022/OneDrive - Shimano/Documents/GitHub/SPL-Alarm-Standardisation-P2-Event-Recording/output/video directory/"

max_segments = 3  # Maximum number of segments to keep in the circular buffer
video_length = 3


class EventRecording:
    def __init__(self):
        self.state = "IDLE"  # Initial state

    def init_camera(self):
        # TODO: Init camera
        if self.state == "":
            self.state = "INITIALISE-EVENT REC. SYSTEM"

    def start_record(self):
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S%f")
        record_video(f"{vid_dir}{timestamp}.mp4", duration=video_length, source=0)
        print(timestamp + " recording completed.")

    def reset(self):
        self.state = "IDLE"
        print("Machine reset. Back to IDLE state.")

    def deleteallvids(self, vid_dir):
        try:
            files = os.listdir(vid_dir)
            for file in files:
                file_path = os.path.join(vid_dir, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
                print(file + " deleted successfully.")

        except OSError:
            print("Error occurred while deleting files.")

    def loopvidremove(self, log_file, vid_dir):
        # Check if the number of files exceeds `max_segments`, delete oldest
        files = sorted(os.listdir(vid_dir))

        if len(files) > max_segments:
            # Remove the oldest file
            oldest_file = os.path.join(vid_dir, files[0])
            os.remove(oldest_file)
            print(files[0] + " deleted successfully.")


# %% Create Object
machine = EventRecording()

# %% Call Functions
while True:
    machine.start_record()
    machine.loopvidremove(log_file, vid_dir)
    # machine.deleteallvids(vid_dir)
