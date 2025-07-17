import pymcprotocol
import time
# Connect to the PLC
plc = pymcprotocol.Type3E()
plc.connect("192.168.3.28", 5055)  # Replace with your PLC's IP

while True:
    # Test writing a single value to D100
    try:
        # Read back the value to confirm
        read_value = plc.batchread_wordunits(headdevice="D100", readsize=1)
        print(f"Read D100 Value: {read_value}")
        time.sleep(1)  # Wait for a second before the next read

    except Exception as e:
        print(f"Error: {e}")
'''

camera.recording = True
Thread(target=camera.record_video).start()  

camera.recording = False

camera.simulate_incident()
'''

if read_value == 1:
    print("PLC is ready for the next operation.")
    camera.recording = True
    Thread(target=camera.record_video).start()  

