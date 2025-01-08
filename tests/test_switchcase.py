# main.py

from test_video_recorder import record_video
from datetime import datetime
import os
import threading

# %% Directoy allocations
# currentTimeStamp = "C:/Users/SPLC4022/OneDrive - Shimano/Documents/GitHub/SPL-Alarm-Standardisation-P2-Event-Recording/logs/"
vid_dir = "C:/Users/SPLC4022/OneDrive - Shimano/Documents/GitHub/SPL-Alarm-Standardisation-P2-Event-Recording/output/video directory/"
savedfootage_dir = "C:/Users/SPLC4022/OneDrive - Shimano/Documents/GitHub/SPL-Alarm-Standardisation-P2-Event-Recording/output/savedfootage/"
max_segments = 3  # Maximum number of segments to keep in the circular buffer


class EventRecording:
    def __init__(self):
        self.state = "IDLE"  # Initial state

    def init_camera(self):
        # TODO: Init camera
        if self.state == "":
            self.state = "INITIALISE-EVENT REC. SYSTEM"

    # def savefootage(self, vid_dir,savedfootage_dir):
    #     # TODO: save last 4 or 5 to a folder. name with timestamp during fault error
    #     files = os.listdir(vid_dir)
    #     shutil.copytree(vid_dir, savedfootage_dir)

    def start_record(self):
        # if self.state == "IDLE":
        #     self.state = "RECORDING"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S%f")
        record_video(f"{vid_dir}{timestamp}.mp4", duration=3, source=0)

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
                print("All files deleted successfully.")

        except OSError:
            print("Error occurred while deleting files.")

    def loopvidremove(self, vid_dir):
        # Check if the number of files exceeds `max_segments`, delete oldest
        files = sorted(os.listdir(vid_dir))
        print(files)
        if len(files) >= max_segments:
            # Remove the oldest file
            oldest_file = os.path.join(vid_dir, files[0])
            os.remove(oldest_file)


# %% Create Object
machine = EventRecording()

# %% Call Functions
while True:
    machine.start_record()
    machine.loopvidremove(vid_dir)

# machine.deleteallvids(vid_dir)
# machine.savefootage(vid_dir,savedfootage_dir)
