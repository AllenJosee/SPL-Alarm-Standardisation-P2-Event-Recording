# main.py

# %%
from processing.video_recorder import record_video
from datetime import datetime


# %% Loop record
while True:
    # %%%   %%%
    dtCurrDate = datetime.now()
    dtCurrDate = dtCurrDate.strftime("%Y%m%d-%H%M%S")

    # %%%%% Call the record_video function with desired parameters %%%%%
    record_video(output_filename=dtCurrDate + ".mp4", duration=3, source=0)
