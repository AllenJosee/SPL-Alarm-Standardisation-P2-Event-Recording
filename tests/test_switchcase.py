from processing.video_recorder import record_video
from datetime import datetime


class EventRecording:
    def __init__(self):
        self.state = "IDLE"  # Initial state

    def video_record(self):
        if self.state == "IDLE":
            self.state = "RECORDING"
            dtCurrDate = datetime.now()
            dtCurrDate = dtCurrDate.strftime("%Y%m%d-%H%M%S")

            # print(dtCurrDate)
            print("Transitioning to RECORDING state.")

        else:
            print("Cannot insert coin. Machine is already processing.")

    def insert_coin(self):
        if self.state == "IDLE":
            self.state = "PROCESSING"
            print("Coin inserted. Transitioning to PROCESSING state.")
        else:
            print("Cannot insert coin. Machine is already processing.")

    def select_product(self):
        if self.state == "PROCESSING":
            self.state = "DISPENSING"
            print("Product selected. Transitioning to DISPENSING state.")
        else:
            print("Cannot select product. Please insert a coin first.")

    def dispense(self):
        if self.state == "DISPENSING":
            self.state = "IDLE"
            print("Dispensing product. Transitioning back to IDLE state.")
        else:
            print("Cannot dispense. Complete previous steps.")

    def reset(self):
        self.state = "IDLE"
        print("Machine reset. Back to IDLE state.")


# %% Create Object
machine = EventRecording()

# %%# Call Functions
machine.video_record()  # Should go to PROCESSING
# machine.select_product()  # Should go to DISPENSING
# machine.dispense()  # Should go back to IDLE
# machine.reset()  # Resets to IDLE
