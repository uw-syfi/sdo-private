import subprocess
import time
import requests
import pytest

def test_start_lego_ui():
    '''Tests that the lego_agent web UI can be launched.'''
    process = None
    try:
        # Start the script in the background
        process = subprocess.Popen(["./scripts/start_lego_ui.sh"])

        # Give the server a moment to start
        time.sleep(10)

        # Check if the UI is running by making a request
        response = requests.get("http://127.0.0.1:8501", timeout=10)

        # Assert that the page is up
        assert response.status_code == 200

    finally:
        # Terminate the process
        if process:
            process.terminate()
            process.wait()
