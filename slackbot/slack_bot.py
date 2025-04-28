import os
from dotenv import load_dotenv
import requests
import csv
from io import StringIO
import matplotlib.pyplot as plt
import datetime
import pytz

load_dotenv()
from slack_sdk.web import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from threading import Event

processed_messages = set()

# 1) App‑level token (starts with xapp‑) for WebSocket connection
app_token = os.environ["SLACK_APP_TOKEN"]
# 2) Bot token (starts with xoxb‑) for Web API calls
bot_token = os.environ["SLACK_BOT_TOKEN"]

# Initialize clients
web_client = WebClient(token=bot_token)
socket_client = SocketModeClient(app_token=app_token, web_client=web_client)

# Send test message on start
web_client.chat_postMessage(channel="C08NTG6CXL5", text="🟢 Bot has started and is now listening for messages.")


def handle_location(user, client):
    try:
        r = requests.get("http://lappy-server:8050/api/track?type=location")
        r.raise_for_status()
        loc = r.json().get("location", {})
        lat = loc.get("lat")
        lon = loc.get("lon")
        map_url = f"https://www.google.com/maps?q={lat},{lon}"
        client.web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"📍 <@{user}> Current :daqcar: location:\n<{map_url}|View on Map>\nLatitude: {lat}\nLongitude: {lon}"
        )
    except Exception as e:
        print("Error fetching location:", e)
        client.web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"❌ <@{user}> Failed to retrieve car location. error: {e}"
        )


def handle_testimage(user):
    print("Test image command received.")
    try:
        web_client.files_upload_v2(
            channel="C08NTG6CXL5",
            file="lappy_test_image.png",
            filename="lappy_test_image.png",
            title="Lappy Test Image",
            initial_comment=f"🖼️ <@{user}> Here's the test image:"
        )
    except Exception as e:
        print("Error uploading image:", e)
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"❌ <@{user}> Failed to upload image. Error: {e}"
        )


def handle_sensors(user):
    print("Fetching unique sensors from InfluxDB...")
    try:
        influx_url = "http://influxwfr:8086"
        influx_org = "WFR"
        influx_bucket = "ourCar"
        influx_token = "s9XkBC7pKOlb92-N9M40qilmxxoBe4wrnki4zpS_o0QSVTuMSQRQBerQB9Zv0YV40tmYayuX3w4G2MNizdy3qw=="

        flux_query = f'''
        from(bucket: "{influx_bucket}")
            |> range(start: -1d)
            |> filter(fn: (r) => r["_measurement"] == "canBus")
            |> distinct(column: "signalName")
        '''

        headers = {
            "Authorization": f"Token {influx_token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv"
        }

        response = requests.post(
            f"{influx_url}/api/v2/query?org={influx_org}",
            headers=headers,
            data=flux_query
        )
        response.raise_for_status()

        csv_data = response.text
        f = StringIO(csv_data)
        reader = csv.DictReader(f)
        signal_names = [row["_value"] for row in reader if "_value" in row]

        if signal_names:
            sensor_list = "\n".join(f"- `{s}`" for s in signal_names)
            web_client.chat_postMessage(
                channel="C08NTG6CXL5",
                text=f"🧪 <@{user}> Unique sensors found:\n{sensor_list}"
            )
        else:
            web_client.chat_postMessage(
                channel="C08NTG6CXL5",
                text=f"⚠️ <@{user}> No sensors found in the past day."
            )

    except Exception as e:
        print("Error fetching sensors from InfluxDB:", e)
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"❌ <@{user}> Failed to fetch sensors. Error: {e}"
        )


def handle_sensor_plot(user, text):
    parts = text.strip().split()
    if len(parts) != 4:
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Usage: `sensor plot SENSORNAME SECONDS`"
        )
        return

    _, _, sensor_name, seconds = parts[0], parts[1], parts[2], parts[3]
    try:
        seconds = int(seconds)
    except ValueError:
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Invalid time range: {seconds}"
        )
        return

    try:
        influx_url = "http://influxwfr:8086"
        influx_org = "WFR"
        influx_bucket = "ourCar"
        influx_token = "s9XkBC7pKOlb92-N9M40qilmxxoBe4wrnki4zpS_o0QSVTuMSQRQBerQB9Zv0YV40tmYayuX3w4G2MNizdy3qw=="

        flux_query = f'''
        from(bucket: "{influx_bucket}")
            |> range(start: -{seconds}s)
            |> filter(fn: (r) => r["_measurement"] == "canBus")
            |> filter(fn: (r) => r["signalName"] == "{sensor_name}")
            |> filter(fn: (r) => r["_field"] == "sensorReading")
            |> aggregateWindow(every: 1s, fn: mean, createEmpty: false)
            |> yield(name: "mean")
        '''

        headers = {
            "Authorization": f"Token {influx_token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv"
        }

        response = requests.post(
            f"{influx_url}/api/v2/query?org={influx_org}",
            headers=headers,
            data=flux_query
        )
        response.raise_for_status()

        csv_data = response.text
        f = StringIO(csv_data)
        reader = csv.DictReader(f)
        times = []
        values = []

        for row in reader:
            if "_time" in row and "_value" in row:
                times.append(datetime.datetime.fromisoformat(row["_time"].replace("Z", "+00:00")))
                values.append(float(row["_value"]))

        if not times or not values:
            web_client.chat_postMessage(
                channel="C08NTG6CXL5",
                text=f"⚠️ <@{user}> No data found for `{sensor_name}` in the past {seconds} seconds."
            )
            return

        plt.figure(figsize=(10, 4))
        plt.plot(times, values, marker='o')
        plt.title(f"Sensor Plot: {sensor_name} ({seconds}s)")
        plt.xlabel("Time")
        plt.ylabel("Value")
        plt.grid(True)
        plt.tight_layout()
        plt.savefig("sensor_plot.png")
        plt.close()

        web_client.files_upload_v2(
            channel="C08NTG6CXL5",
            file="sensor_plot.png",
            filename="sensor_plot.png",
            title=f"{sensor_name} - last {seconds} sec",
            initial_comment=f"📊 <@{user}> Here's the plot for `{sensor_name}` over the past {seconds} seconds:"
        )

    except Exception as e:
        print("Error plotting sensor:", e)
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"❌ <@{user}> Failed to generate plot. Error: {e}"
        )

def handle_sensor_plot_range(user, text):
    parts = text.strip().split()
    if len(parts) != 6 or parts[3].lower() != "range":
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Usage: `sensor plot SENSORNAME range YYYYMMDDHHMMZ YYYYMMDDHHMMZ`"
        )
        return

    sensor_name = parts[2]
    start_raw = parts[4]
    end_raw = parts[5]

    def parse_time(s):
        tz = pytz.UTC if s.endswith("Z") else pytz.timezone("America/Toronto")
        base = s[:-1]
        dt = datetime.datetime.strptime(base, "%Y%m%d%H%M")
        return tz.localize(dt)

    try:
        start_time = parse_time(start_raw)
        end_time = parse_time(end_raw)
    except Exception:
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Invalid time format. Use `YYYYMMDDHHMMZ` for UTC or `YYYYMMDDHHMML` for local time."
        )
        return

    try:
        influx_url = "http://influxwfr:8086"
        influx_org = "WFR"
        influx_bucket = "ourCar"
        influx_token = "s9XkBC7pKOlb92-N9M40qilmxxoBe4wrnki4zpS_o0QSVTuMSQRQBerQB9Zv0YV40tmYayuX3w4G2MNizdy3qw=="

        flux_query = f'''
        from(bucket: "{influx_bucket}")
            |> range(start: {start_time.isoformat()}, stop: {end_time.isoformat()})
            |> filter(fn: (r) => r["_measurement"] == "canBus")
            |> filter(fn: (r) => r["signalName"] == "{sensor_name}")
            |> filter(fn: (r) => r["_field"] == "sensorReading")
            |> aggregateWindow(every: 1s, fn: mean, createEmpty: false)
            |> yield(name: "mean")
        '''

        headers = {
            "Authorization": f"Token {influx_token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv"
        }

        response = requests.post(
            f"{influx_url}/api/v2/query?org={influx_org}",
            headers=headers,
            data=flux_query
        )
        response.raise_for_status()

        csv_data = response.text
        f = StringIO(csv_data)
        reader = csv.DictReader(f)
        times = []
        values = []

        for row in reader:
            if "_time" in row and "_value" in row:
                times.append(datetime.datetime.fromisoformat(row["_time"].replace("Z", "+00:00")))
                values.append(float(row["_value"]))

        if not times or not values:
            web_client.chat_postMessage(
                channel="C08NTG6CXL5",
                text=f"⚠️ <@{user}> No data found for `{sensor_name}` in that range."
            )
            return

        plt.figure(figsize=(10, 4))
        plt.plot(times, values, marker='o')
        plt.title(f"Sensor Plot: {sensor_name} (range)")
        plt.xlabel("Time")
        plt.ylabel("Value")
        plt.grid(True)
        plt.tight_layout()
        plt.savefig("sensor_plot.png")
        plt.close()

        web_client.files_upload_v2(
            channel="C08NTG6CXL5",
            file="sensor_plot.png",
            filename="sensor_plot.png",
            title=f"{sensor_name} - range plot",
            initial_comment=f"📊 <@{user}> Plot for `{sensor_name}` from {start_time} to {end_time}:"
        )

    except Exception as e:
        print("Error plotting sensor:", e)
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"❌ <@{user}> Failed to generate plot. Error: {e}"
        )

def handle_sensor_timeline(user, text):
    parts = text.strip().split()
    if len(parts) not in (2, 3):
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Usage: `sensor timeline [SENSORNAME]`"
        )
        return

    if len(parts) == 3:
        sensor_name = parts[2]
        warning_message = ""
    else:
        sensor_name = "INV_DC_Bus_Voltage"
        warning_message = f"⚠️ <@{user}> No sensor specified. Defaulting to `INV_DC_Bus_Voltage`.\n"

    try:
        influx_url = "http://influxwfr:8086"
        influx_org = "WFR"
        influx_bucket = "ourCar"
        influx_token = "s9XkBC7pKOlb92-N9M40qilmxxoBe4wrnki4zpS_o0QSVTuMSQRQBerQB9Zv0YV40tmYayuX3w4G2MNizdy3qw=="

        flux_query = f'''
        from(bucket: "{influx_bucket}")
            |> range(start: -24h)
            |> filter(fn: (r) => r["_measurement"] == "canBus")
            {f'|> filter(fn: (r) => r["signalName"] == "{sensor_name}")' if sensor_name else ''}
            |> filter(fn: (r) => r["_field"] == "sensorReading")
            |> keep(columns: ["_time"])
        '''

        headers = {
            "Authorization": f"Token {influx_token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv"
        }

        response = requests.post(
            f"{influx_url}/api/v2/query?org={influx_org}",
            headers=headers,
            data=flux_query
        )
        response.raise_for_status()

        csv_data = response.text
        f = StringIO(csv_data)
        reader = csv.DictReader(f)
        times = [datetime.datetime.fromisoformat(row["_time"].replace("Z", "+00:00")) for row in reader if "_time" in row]

        if not times:
            web_client.chat_postMessage(
                channel="C08NTG6CXL5",
                text=f"⚠️ <@{user}> No data found for `{sensor_name}` in the past 24 hours."
            )
            return

        times.sort()
        timeline = []
        current_start = times[0]

        for i in range(1, len(times)):
            if (times[i] - times[i-1]).total_seconds() > 60:
                timeline.append((current_start, times[i-1]))
                current_start = times[i]
        timeline.append((current_start, times[-1]))

        fig, ax = plt.subplots(figsize=(10, 2))
        for start, end in timeline:
            ax.plot([start, end], [1, 1], lw=6)
        ax.set_yticks([])
        ax.set_title(f"Sensor Timeline: {sensor_name} (Past 24h)")
        ax.set_xlabel("Time")
        fig.autofmt_xdate()
        plt.tight_layout()
        plt.savefig("sensor_timeline.png")
        plt.close()

        toronto_tz = pytz.timezone("America/Toronto")
        segments = "| Start (Zulu) | Start (Toronto) | End (Zulu) | End (Toronto) |\n"
        segments += "|--------------|------------------|------------|----------------|\n"
        segments += "\n".join(
            f"| {s.strftime('%Y%m%d%H%M')}Z | {s.astimezone(toronto_tz).strftime('%Y%m%d%H%M')}L | {e.strftime('%Y%m%d%H%M')}Z | {e.astimezone(toronto_tz).strftime('%Y%m%d%H%M')}L |"
            for s, e in timeline
        )

        web_client.files_upload_v2(
            channel="C08NTG6CXL5",
            file="sensor_timeline.png",
            filename="sensor_timeline.png",
            title=f"{sensor_name} Timeline",
            initial_comment=f"{warning_message}📈 <@{user}> Activity timeline for `{sensor_name}` in the past 24h:\n```md\n{segments}\n```"
        )

    except Exception as e:
        print("Error generating timeline:", e)
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"❌ <@{user}> Failed to generate timeline. Error: {e}"
        )

def handle_help(user):
    help_message = (
        f"📘 <@{user}> Available Commands:\n"
        "```\n"
        "!location                     - Show the current car location on the map\n"
        "!testimage                   - Upload a test image\n"
        "!sensors                     - List all unique sensors detected in the past 24h\n"
        "!sensor plot NAME SECONDS    - Plot sensor data for the last N seconds\n"
        "!sensor plot NAME range START END\n"
        "                             - Plot sensor data between specified timestamps (YYYYMMDDHHMMZ or L)\n"
        "!sensor timeline [NAME]      - Show data availability timeline in the past 24h, with optional sensor filter\n"
        "!help                        - Show this help message\n"
        "Z is for UTC, L is for local time\n"
        "```"
    )
    web_client.chat_postMessage(channel="C08NTG6CXL5", text=help_message)

def process_events(client: SocketModeClient, req: SocketModeRequest):
    if req.type == "events_api":
        event = req.payload.get("event", {})
        if (
                event.get("type") == "message"
                and event.get("subtype") is None
                and event.get("channel") == "C08NTG6CXL5"
        ):
            msg_ts = event.get("ts")
            if msg_ts in processed_messages:
                return
            processed_messages.add(msg_ts)

            user = event.get("user")
            if user == "U08P8KS8K25":  # this is the bot user id
                return
            text = event.get("text")
            print(f"👤 {user}: {text}")
            if "!" not in text:
                return
            # Remove !
            text = text[1:]
            if "location" in text.lower():
                handle_location(user, client)
            elif "testimage" in text.lower():
                handle_testimage(user)
            elif text.lower().startswith("sensor plot") and "range" in text.lower():
                handle_sensor_plot_range(user, text)
            elif text.lower().startswith("sensor plot"):
                handle_sensor_plot(user, text)
            elif "sensors" in text.lower():
                handle_sensors(user)
            elif "help" in text.lower():
                handle_help(user)
            elif text.lower().startswith("sensor timeline"):
                handle_sensor_timeline(user, text)
        client.send_socket_mode_response(
            SocketModeResponse(envelope_id=req.envelope_id)
        )


# Register the listener
socket_client.socket_mode_request_listeners.append(process_events)

# Connect and block forever
socket_client.connect()
Event().wait()