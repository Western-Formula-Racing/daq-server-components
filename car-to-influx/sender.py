import re
import time
import requests
import json

# Configuration
FILE = "testing_data/CanTraceJuly11.txt"
URL = "http://3.98.181.12:8085/can"
BATCH_SIZE = 450   # frames per POST
RATE = 1.0         # batches per second

def parse_raw_frame(line):
    """
    Parse a raw CAN-line into a dict with fields:
      index, can_id, dlc, data_bytes (list of ints), timestamp, direction
    """
    pattern = r'\s*(\d+)\s+(\w+)(?:\s+X)?\s+(\d+)\s+([0-9\s]+)\s+(\d+\.\d+)\s+([RX])'
    m = re.match(pattern, line)
    if not m:
        return None
    idx, cid, dlc, data_bytes, ts, dir_ = m.groups()
    return {
        "index":     int(idx),
        "can_id":    cid,
        "dlc":       int(dlc),
        "data_bytes": [int(b) for b in data_bytes.split() if b],
        "timestamp": float(ts),
        "direction": dir_
    }

def load_lines(filepath):
    with open(filepath, 'r') as f:
        return [l for l in f.read().splitlines() if l.strip()]

def chunkify(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i+size]

def send_batches(lines, url, batch_size, delay):
    session = requests.Session()
    for batch in chunkify(lines, batch_size):
        frames = []
        for l in batch:
            parsed = parse_raw_frame(l)
            if not parsed:
                continue
            # Map to server's expected keys
            frames.append({
                "id": parsed["can_id"],
                "data": parsed["data_bytes"],
                "timestamp": parsed["timestamp"]
            })
        if not frames:
            continue

        try:
            resp = session.post(url, json=frames, timeout=5)
            resp.raise_for_status()
            print(f"Sent {len(frames)} frames → {resp.status_code}")
        except requests.HTTPError as e:
            print(f"Error sending batch: {e} → status {resp.status_code}")
            print("Server response:", resp.text)
        except Exception as e:
            print(f"Unexpected error: {e}")

        # print server response
        print("Server response:", resp.text)

        time.sleep(delay)

if __name__ == "__main__":
    lines = load_lines(FILE)
    print(f"Loaded {len(lines)} raw lines from {FILE}")
    delay = 1.0 / RATE
    send_batches(lines, URL, BATCH_SIZE, delay)
