import sys
import os
import logging
from datetime import datetime

class DualLogger(object):
    """
    Writes to both stdout/stderr and a file.
    """
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, "a", encoding='utf-8')

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()

def setup_logging(script_name="quant_tool"):
    """
    Sets up logging to a file in the ../../log directory relative to this script.
    Assumes this script is in api/quant/ or similar depth.
    """
    # Find log directory: assume api/quant/ -> ... -> log/
    # If run from root (vibetrader/), log/ is just log/
    
    # Try to find workspace root
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # api/quant -> api -> vibetrader
    workspace_root = os.path.dirname(os.path.dirname(current_dir)) 
    log_dir = os.path.join(workspace_root, 'log')
    
    if not os.path.exists(log_dir):
        # Fallback if assumptions fail
        log_dir = "log"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"{script_name}_{timestamp}.log"
    log_path = os.path.join(log_dir, log_filename)
    
    print(f"[Logging] Output will be saved to: {log_path}")
    
    # Redirect stdout and stderr
    sys.stdout = DualLogger(log_path)
    # sys.stderr = DualLogger(log_path) # Optional, strictly speaking stderr might be errors only
    
    return log_path
