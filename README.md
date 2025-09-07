This repo is for the helper functions on the AWS server, as well as an installer script for local server replica

## Port

lappy-server: 8050

car-to-influx: 8085

InfluxDB (influxwfr): 8086

The above helpers communicate over Docker network "datalink"

```docker network connect datalink influxwfr```



MangoDB: 3000 (not in this repo)



## No Port Assigned

Slackbot



# car-to-influx (CAN Frame Listener) 8085

To start the docker:

``` 
docker run --name car-to-influx \
  --network datalink \
  -v /home/ubuntu/car-to-influx:/app \
  -p 8085:8085 \
  car-to-influx:latest
```

If modification is made to the code, simply restart the container. If new package is needed, modify Dockerfile and rebuild the container

```
cd car-to-influx
docker build --no-cache -t car-to-influx:latest .
```



This listener accepts CAN frame(s) from the car, and write them into InfluxDB

The server exposes a single HTTP endpoint for ingesting CAN messages:

```
POST http://3.98.181.12:8085/can
```

## Table of Contents:

1. How is timestamp handled
2. Data Format
3. Example Code
4. Test Message in Terminal/PowerShell

---

### How is timestamp handled

* Absolute epoch seconds
  If the incoming timestamp is greater than 946_684_800 (which is 2000‑01‑01 00:00:00 UTC in Unix time), the code treats it as a true Unix epoch timestamp

  ``````python
  if ts > 946_684_800:
      return datetime.fromtimestamp(ts, timezone.utc)
  ``````

* Relative seconds

  If the incoming timestamp is smaller than that cutoff (< 946 684 800), the code assumes it’s a relative counter (seconds elapsed since the first frame). It then:
  	1.	Anchors the first relative timestamp to “now”
  	2.	Re‑anchors if the counter resets by more than 60 s
  	3.	Adds the elapsed relative seconds to the anchor to produce a datetime

**When possible, use absolute epoch seconds**



### Data Format

#### Single Message

```json
{
  "messages": [
    {
      "id": "0x1A3",      // CAN ID as string (hex or decimal)
      "data": [10, 20, 30, 40, 50, 60, 70, 80],  // Data bytes as array of integers
      "timestamp": 1648123456.789  // Unix timestamp in seconds
    }
  ]
}
```

#### Multiple Messages

```json
{
  "messages": [
    {
      "id": "0x1A3",
      "data": [10, 20, 30, 40, 50, 60, 70, 80],
      "timestamp": 1648123456.789
    },
    {
      "id": "26",         // Decimal ID also accepted
      "data": [1, 2, 3, 4, 5, 6, 7, 8],
      "timestamp": 1648123456.790
    }
  ]
}
```



### Example Code

#### Python

```python
import requests
import json
import os
import time

URL = "http://3.98.181.12:8085/can"
RETRY_FILE = "retry_buffer.json"
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

def send_payload(payload):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(URL, json=payload, timeout=5)
            if resp.status_code < 400:
                print(f"Success: {resp.status_code}")
                return True
            else:
                print(f"Server error {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"Attempt {attempt} failed: {e}")
        time.sleep(RETRY_DELAY)
    return False

def load_retry_buffer():
    if os.path.exists(RETRY_FILE):
        with open(RETRY_FILE, "r") as f:
            return json.load(f)
    return []

def save_retry_buffer(buffer):
    with open(RETRY_FILE, "w") as f:
        json.dump(buffer, f)

def flush_retry_buffer():
    buffer = load_retry_buffer()
    if not buffer:
        return
    print(f"Retrying {len(buffer)} buffered payload(s)…")
    remaining = []
    for payload in buffer:
        if not send_payload(payload):
            remaining.append(payload)
    save_retry_buffer(remaining)

def main():
    flush_retry_buffer()

    # Your new payload here
    payload = {
        "messages": [
            {
                "id": "171",
                "data": [0, 0, 0, 0, 0, 0, 0, 0],
                "timestamp": 1648123456.789
            }
        ]
    }

    if not send_payload(payload):
        print("Storing payload to buffer")
        buffer = load_retry_buffer()
        buffer.append(payload)
        save_retry_buffer(buffer)

if __name__ == "__main__":
    main()
```



#### C++ESP32 Arduino C++ (with in-memory + flash buffer via SPIFFS)

==ChatGPT wrote the code, use with caution==

- Tries up to 3 times
- Buffers unsent messages in SPIFFS /retry_buffer.txt (as JSON lines)

```c++
#include <SPIFFS.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
```

Setup SPIFFS in setup()

```c++
if (!SPIFFS.begin(true)) {
  Serial.println("SPIFFS mount failed");
}
```

Helper: Write payload to buffer file

```c++
void appendToBuffer(const String& jsonLine) {
  File f = SPIFFS.open("/retry_buffer.txt", FILE_APPEND);
  if (!f) return;
  f.println(jsonLine);
  f.close();
}
```

Helper: Retry sending stored messages

```c++
void flushBuffer(const char* url) {
  File f = SPIFFS.open("/retry_buffer.txt", FILE_READ);
  if (!f) return;

  File temp = SPIFFS.open("/tmp.txt", FILE_WRITE);
  while (f.available()) {
    String line = f.readStringUntil('\n');
    HTTPClient http;
    http.begin(url);
    http.addHeader("Content-Type", "application/json");
    int code = http.POST(line);
    http.end();
    if (code < 200 || code >= 400) {
      temp.println(line);  // retain if failed
    }
  }
  f.close();
  temp.close();
  SPIFFS.remove("/retry_buffer.txt");
  SPIFFS.rename("/tmp.txt", "/retry_buffer.txt");
}
```

Example use:

```c++
flushBuffer(serverUrl);

// prepare `String body = ...` like before
HTTPClient http;
http.begin(serverUrl);
http.addHeader("Content-Type", "application/json");

int code = http.POST(body);
http.end();

if (code < 200 || code >= 400) {
  Serial.printf("POST failed (%d), buffering...\n", code);
  appendToBuffer(body);
} else {
  Serial.println("POST success");
}
```



------



### Give it a try!

This is some data from 2022 (UTC 2022-03-24 12:04:16.789)

```bash
curl -X POST http://3.98.181.12:8085/can \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      { "id": "171",  "data": [0, 0, 0, 0, 0, 0, 0, 0], "timestamp": 1648123456.789 },
      { "id": "172",  "data": [0, 0, 0, 0, 254, 219, 0, 0], "timestamp": 1648123456.789 },
      { "id": "173",  "data": [0, 0, 0, 0, 0, 0, 0, 0], "timestamp": 1648123456.789 },
      { "id": "177",  "data": [70, 6, 186, 249, 0, 0, 0, 0], "timestamp": 1648123456.789 },
      { "id": "176",  "data": [0, 0, 0, 0, 0, 0, 0, 0], "timestamp": 1648123456.789 },
      { "id": "192",  "data": [0, 0, 0, 0, 1, 0, 0, 0], "timestamp": 1648123456.789 },
      { "id": "176",  "data": [0, 0, 2, 0, 0, 0, 0, 0], "timestamp": 1648123456.789 },
      { "id": "514",  "data": [120, 0, 6, 0, 0, 0, 0, 0], "timestamp": 1648123456.789 }
    ]
  }'
```

Expect: 

```
{"status":"ok","written":30}
```



