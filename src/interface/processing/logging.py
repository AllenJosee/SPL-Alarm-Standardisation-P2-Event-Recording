import os
import logging
from datetime import datetime
import pandas as pd

# Configure logging to write to a text file
logging.basicConfig(
    filename="file_creation_log.txt", level=logging.INFO, format="%(message)s"
)


def check_and_create_excel_file(filename):
    """
    Checks if an Excel file exists. If not, logs a message indicating its absence
    and creates the file.

    Parameters:
    - filename (str): The name of the Excel file to check and potentially create.
    """
    if os.path.exists(filename):
        logging.info(f"{filename} exists as of {datetime.now()}")
    else:
        # Log the file absence
        logging.info(
            f"{filename} does not exist as of {datetime.now()}. Creating a new file."
        )

        # Create a sample DataFrame to save as the new Excel file
        data = pd.DataFrame({"Name": ["Alice", "Bob", "Charlie"], "Age": [25, 30, 35]})
        data.to_excel(filename, index=False)

        # Log the file creation
        logging.info(f"{filename} created at {datetime.now()}")


# Example usage:
check_and_create_excel_file("sample_file.xlsx")
