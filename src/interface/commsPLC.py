import pymcprotocol

# Connect to the PLC
plc = pymcprotocol.Type3E()
plc.connect("169.254.111.50", 5007)  # Replace with your PLC's IP

while True:
    # Test writing a single value to D100
    try:
        # Read back the value to confirm
        read_value = plc.batchread_wordunits(headdevice="D100", readsize=1)
        print(f"Read D100 Value: {read_value}")

    except Exception as e:
        print(f"Error: {e}")
