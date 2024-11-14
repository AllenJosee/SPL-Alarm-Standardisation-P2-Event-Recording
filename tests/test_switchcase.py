from test_video_recorder import record_video
from datetime import datetime

# %% Directoy allocations
log_file = "/home/pi/dashcam/video_log.txt"
vid_dir = "C:/Users\SPLC4022\OneDrive - Shimano\Documents\GitHub\SPL-Alarm-Standardisation-P2-Event-Recording\output/video directory/"
max_segments = 5  # Maximum number of segments to keep in the circular buffer


# %% Class
class EventRecording:
    def __init__(self):
        self.state = "IDLE"  # Initial state

    def init_camera(self):
        # TODO: Init camera
        if self.state == "":
            self.state = "INITIALISE-EVENT REC. SYSTEM"

    def start_record(self):
        if self.state == "IDLE":
            self.state = "RECORDING"

            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S%f")
            record_video(f"{vid_dir}{timestamp}.mp4", duration=3, source=0)
            print("Transitioning to RECORDING state.")
            # TODO: stop video recording if stopped in HMI

        else:
            print("Cannot insert coin. Machine is already processing.")

    def reset(self):
        # TODO: Clear/reset any needed things
        self.state = "IDLE"
        print("Machine reset. Back to IDLE state.")

    def test1(self):
        pass


# %% Create Object
machine = EventRecording()

# %%# Call Functions
machine.start_record()
