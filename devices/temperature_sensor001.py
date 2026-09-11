import os
import time
import random
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:5000")
USERNAME = os.getenv("BASIC_AUTH_USERNAME", "admin")
PASSWORD = os.getenv("BASIC_AUTH_PASSWORD", "password123")

API_URL = f"{API_BASE_URL}/api/measurements"
DEVICE_ID = "device_001"


def send_measurement():
    now = datetime.now()

    payload = {
        "device_id": DEVICE_ID,
        "sensor_type": "temperature",
        "device_unix_time": int(time.time()),
        "device_local_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "data": {
            "temperature": round(random.uniform(18.0, 28.0), 2)
        }
    }

    try:
        response = requests.post(
            API_URL,
            json=payload,
            auth=(USERNAME, PASSWORD)
        )

        if response.status_code == 201:
            print(f"[{DEVICE_ID}] Sent: {payload['data']}")
        else:
            print(f"Error {response.status_code}: {response.text}")

    except Exception as e:
        print(f"Failed: {e}")


if __name__ == "__main__":
    print("Temperature sensor started. Press CTRL+C to stop.\n")
    while True:
        send_measurement()
        time.sleep(5)