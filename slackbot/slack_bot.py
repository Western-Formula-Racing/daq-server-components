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
# web_client.chat_postMessage(channel="C08NTG6CXL5", text="🟢 Bot has started and is now listening for messages.") # You might want to re-enable this if needed


def handle_location(user, client):
    try:
        r = requests.get("http://lappy-server:8050/api/track?type=location")
        r.raise_for_status()
        loc = r.json().get("location", {})
        lat = loc.get("lat")
        lon = loc.get("lon")
        map_url = f"http://www.google.com/maps/place/{lat},{lon}/@{lat},{lon},17z"  # Corrected map URL
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
        # Ensure lappy_test_image.png exists in the same directory as the script or provide full path
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
                text=f"🧪 <@{user}> Unique sensors found in the past day:\n{sensor_list}"
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


def download_raw_sensor_data(user, web_client, sensor_name, flux_query_raw, filename_suffix_tag):
    """
    Fetches raw sensor data from InfluxDB and uploads it as a CSV file to Slack.
    """
    try:
        influx_url = "http://influxwfr:8086"
        influx_org = "WFR"
        influx_bucket = "ourCar"
        influx_token = "s9XkBC7pKOlb92-N9M40qilmxxoBe4wrnki4zpS_o0QSVTuMSQRQBerQB9Zv0YV40tmYayuX3w4G2MNizdy3qw=="

        headers = {
            "Authorization": f"Token {influx_token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv"
        }

        print(f"Executing raw data query for {sensor_name}:\n{flux_query_raw}")  # For debugging
        response = requests.post(
            f"{influx_url}/api/v2/query?org={influx_org}",
            headers=headers,
            data=flux_query_raw
        )
        response.raise_for_status()
        csv_content = response.text

        # Check if the CSV content has actual data beyond InfluxDB metadata comments
        # A typical InfluxDB CSV response with no data rows will still have several lines starting with '#'
        # and a header row. So, > 4 lines usually means at least one data row or just headers.
        # A more robust check might parse the CSV, but this is a quick check.
        if not csv_content.strip() or len(
                csv_content.splitlines()) < 5:  # Assuming at least 3 metadata, 1 header, 1 data row
            web_client.chat_postMessage(
                channel="C08NTG6CXL5",
                text=f"⚠️ <@{user}> No raw data found for `{sensor_name}` for the specified criteria to download."
            )
            return

        csv_filename = f"raw_data_{sensor_name}_{filename_suffix_tag}.csv"
        web_client.files_upload_v2(
            channel="C08NTG6CXL5",
            content=csv_content.encode('utf-8'),  # Ensure content is bytes
            filename=csv_filename,
            title=f"Raw data for {sensor_name} ({filename_suffix_tag})",
            initial_comment=f"📄 <@{user}> Here's the raw CSV data for `{sensor_name}`:"
        )

    except Exception as e:
        print(f"Error downloading raw sensor data for {sensor_name}:", e)
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"❌ <@{user}> Failed to download raw CSV data for `{sensor_name}`. Error: {e}"
        )


def handle_sensor_plot(user, text):
    original_text = text  # Keep original text for flag checking
    download_requested = "--d" in original_text

    if download_requested:
        text = original_text.replace("--d", "").strip()  # Remove flag for parsing

    parts = text.strip().split()
    # Expected: sensor plot SENSORNAME SECONDS
    if len(parts) != 4 or parts[0] != "sensor" or parts[1] != "plot":
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Usage: `sensor plot SENSORNAME SECONDS [--d]`"
        )
        return

    sensor_name = parts[2]
    seconds_str = parts[3]

    try:
        seconds = int(seconds_str)
        if seconds <= 0:
            raise ValueError("Seconds must be positive")
    except ValueError:
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Invalid time range: `{seconds_str}`. Must be a positive integer."
        )
        return

    influx_bucket = "ourCar"  # Define bucket, can be centralized later

    if download_requested:
        raw_flux_query = f'''
        from(bucket: "{influx_bucket}")
            |> range(start: -{seconds}s)
            |> filter(fn: (r) => r["_measurement"] == "canBus")
            |> filter(fn: (r) => r["signalName"] == "{sensor_name}")
            |> filter(fn: (r) => r["_field"] == "sensorReading")
            |> yield(name: "raw")
        '''
        download_raw_sensor_data(user, web_client, sensor_name, raw_flux_query, f"last_{seconds}s")
        return

    # --- Existing plotting logic ---
    try:
        influx_url = "http://influxwfr:8086"
        influx_org = "WFR"
        influx_token = "s9XkBC7pKOlb92-N9M40qilmxxoBe4wrnki4zpS_o0QSVTuMSQRQBerQB9Zv0YV40tmYayuX3w4G2MNizdy3qw=="

        # Query for plotting (with aggregation)
        plot_flux_query = f'''
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
            data=plot_flux_query
        )
        response.raise_for_status()

        csv_data = response.text
        f = StringIO(csv_data)
        reader = csv.DictReader(f)
        times = []
        values = []

        for row in reader:
            if "_time" in row and "_value" in row and row["_value"]:  # Ensure _value is not empty
                try:
                    times.append(datetime.datetime.fromisoformat(row["_time"].replace("Z", "+00:00")))
                    values.append(float(row["_value"]))
                except ValueError:
                    print(f"Skipping row with invalid data: {row}")  # Log problematic row
                    continue

        if not times or not values:
            web_client.chat_postMessage(
                channel="C08NTG6CXL5",
                text=f"⚠️ <@{user}> No data found for `{sensor_name}` in the past {seconds} seconds to plot."
            )
            return

        plt.figure(figsize=(10, 4))
        plt.plot(times, values, marker='o', linestyle='-')
        plt.title(f"Sensor Plot: {sensor_name} (Last {seconds}s, 1s Mean)")
        plt.xlabel("Time (UTC)")
        plt.ylabel(f"Value ({sensor_name})")
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
        print(f"Error plotting sensor {sensor_name}:", e)
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"❌ <@{user}> Failed to generate plot for `{sensor_name}`. Error: {e}"
        )


def handle_sensor_plot_range(user, text):
    original_text = text
    download_requested = "--d" in original_text

    if download_requested:
        text = original_text.replace("--d", "").strip()

    parts = text.strip().split()
    # Expected: sensor plot SENSORNAME range START END
    if len(parts) != 6 or parts[0] != "sensor" or parts[1] != "plot" or parts[3] != "range":
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Usage: `sensor plot SENSORNAME range YYYYMMDDHHMMZ|L YYYYMMDDHHMMZ|L [--d]`"
        )
        return

    sensor_name = parts[2]
    start_raw = parts[4]
    end_raw = parts[5]

    def parse_time(s):
        # Determine timezone: 'Z' for UTC, 'L' for local (America/Toronto)
        if s.upper().endswith("Z"):
            tz = pytz.UTC
            base = s[:-1]
        elif s.upper().endswith("L"):
            tz = pytz.timezone("America/Toronto")
            base = s[:-1]
        else:  # Default to UTC if no specifier, or raise error
            # For simplicity, let's assume UTC if no suffix, but better to be explicit
            # raise ValueError("Time string must end with Z (UTC) or L (Local)")
            tz = pytz.UTC  # Or handle as error
            base = s
        dt_naive = datetime.datetime.strptime(base, "%Y%m%d%H%M")
        return tz.localize(dt_naive)

    try:
        start_time = parse_time(start_raw)
        end_time = parse_time(end_raw)
        if start_time >= end_time:
            web_client.chat_postMessage(
                channel="C08NTG6CXL5",
                text=f"⚠️ <@{user}> Start time (`{start_raw}`) must be before end time (`{end_raw}`)."
            )
            return
    except ValueError as e:
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Invalid time format. Use `YYYYMMDDHHMMZ` for UTC or `YYYYMMDDHHMML` for local. Error: {e}"
        )
        return

    influx_bucket = "ourCar"  # Define bucket

    if download_requested:
        raw_flux_query = f'''
        from(bucket: "{influx_bucket}")
            |> range(start: {start_time.isoformat()}, stop: {end_time.isoformat()})
            |> filter(fn: (r) => r["_measurement"] == "canBus")
            |> filter(fn: (r) => r["signalName"] == "{sensor_name}")
            |> filter(fn: (r) => r["_field"] == "sensorReading")
            |> yield(name: "raw")
        '''
        download_raw_sensor_data(user, web_client, sensor_name, raw_flux_query, f"{start_raw}_to_{end_raw}")
        return

    # --- Existing plotting logic ---
    try:
        influx_url = "http://influxwfr:8086"
        influx_org = "WFR"
        influx_token = "s9XkBC7pKOlb92-N9M40qilmxxoBe4wrnki4zpS_o0QSVTuMSQRQBerQB9Zv0YV40tmYayuX3w4G2MNizdy3qw=="

        # Query for plotting (with aggregation)
        plot_flux_query = f'''
        from(bucket: "{influx_bucket}")
            |> range(start: {start_time.isoformat()}, stop: {end_time.isoformat()})
            |> filter(fn: (r) => r["_measurement"] == "canBus")
            |> filter(fn: (r) => r["signalName"] == "{sensor_name}")
            |> filter(fn: (r) => r["_field"] == "sensorReading")
            |> aggregateWindow(every: 1s, fn: mean, createEmpty: false) 
            |> yield(name: "mean")
        '''
        # The aggregation interval for range plots might need adjustment based on the range duration.
        # For very long ranges, 1s might be too granular. For now, keeping it 1s.

        headers = {
            "Authorization": f"Token {influx_token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv"
        }

        response = requests.post(
            f"{influx_url}/api/v2/query?org={influx_org}",
            headers=headers,
            data=plot_flux_query
        )
        response.raise_for_status()

        csv_data = response.text
        f = StringIO(csv_data)
        reader = csv.DictReader(f)
        times = []
        values = []

        for row in reader:
            if "_time" in row and "_value" in row and row["_value"]:  # Ensure _value is not empty
                try:
                    times.append(datetime.datetime.fromisoformat(row["_time"].replace("Z", "+00:00")))
                    values.append(float(row["_value"]))
                except ValueError:
                    print(f"Skipping row with invalid data during range plot: {row}")
                    continue

        if not times or not values:
            web_client.chat_postMessage(
                channel="C08NTG6CXL5",
                text=f"⚠️ <@{user}> No data found for `{sensor_name}` in the range {start_raw} to {end_raw} to plot."
            )
            return

        plt.figure(figsize=(10, 4))
        plt.plot(times, values, marker='o', linestyle='-')
        plt.title(f"Sensor Plot: {sensor_name} ({start_raw} to {end_raw}, 1s Mean)")
        plt.xlabel("Time (UTC)")
        plt.ylabel(f"Value ({sensor_name})")
        plt.grid(True)
        plt.tight_layout()
        plt.savefig("sensor_plot_range.png")  # Use a different name to avoid conflict
        plt.close()

        web_client.files_upload_v2(
            channel="C08NTG6CXL5",
            file="sensor_plot_range.png",
            filename=f"{sensor_name}_range_plot.png",
            title=f"{sensor_name} - {start_raw} to {end_raw}",
            initial_comment=f"📊 <@{user}> Plot for `{sensor_name}` from {start_time.strftime('%Y-%m-%d %H:%M %Z')} to {end_time.strftime('%Y-%m-%d %H:%M %Z')}:"
        )

    except Exception as e:
        print(f"Error plotting sensor {sensor_name} for range:", e)
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"❌ <@{user}> Failed to generate plot for `{sensor_name}` (range). Error: {e}"
        )


def handle_sensor_timeline(user, text):
    parts = text.strip().split()
    # Expected: sensor timeline [SENSORNAME]
    if not (parts[0] == "sensor" and parts[1] == "timeline" and (len(parts) == 2 or len(parts) == 3)):
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Usage: `sensor timeline [SENSORNAME]`"
        )
        return

    sensor_name_filter = ""
    default_sensor_message = ""
    if len(parts) == 3:
        sensor_name_filter = parts[2]
        sensor_display_name = sensor_name_filter
    else:
        sensor_name_filter = "INV_DC_Bus_Voltage"  # Default sensor
        sensor_display_name = sensor_name_filter
        default_sensor_message = f"⚠️ No sensor specified. Defaulting to `{sensor_display_name}`.\n"

    try:
        influx_url = "http://influxwfr:8086"
        influx_org = "WFR"
        influx_bucket = "ourCar"
        influx_token = "s9XkBC7pKOlb92-N9M40qilmxxoBe4wrnki4zpS_o0QSVTuMSQRQBerQB9Zv0YV40tmYayuX3w4G2MNizdy3qw=="

        # Query to get timestamps for the timeline
        # No aggregation needed here, just the time points where data exists
        flux_query = f'''
        from(bucket: "{influx_bucket}")
            |> range(start: -24h)
            |> filter(fn: (r) => r["_measurement"] == "canBus")
            |> filter(fn: (r) => r["signalName"] == "{sensor_name_filter}")
            |> filter(fn: (r) => r["_field"] == "sensorReading")
            |> keep(columns: ["_time"]) // Only need time
            |> sort(columns: ["_time"]) // Ensure times are sorted
        '''
        # Removed distinct(column: "_time") as sort should handle order, and multiple readings at same exact nano might be valid

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
        for row in reader:
            if "_time" in row:
                try:
                    times.append(datetime.datetime.fromisoformat(row["_time"].replace("Z", "+00:00")))
                except ValueError:
                    print(f"Skipping invalid time format in timeline data: {row['_time']}")
                    continue

        if not times:
            web_client.chat_postMessage(
                channel="C08NTG6CXL5",
                text=f"{default_sensor_message}⚠️ <@{user}> No data found for `{sensor_display_name}` in the past 24 hours to create a timeline."
            )
            return

        # times should already be sorted from the query, but an explicit sort here is safe
        times.sort()

        timeline_segments = []
        if not times:  # Should be caught above, but as a safeguard
            web_client.chat_postMessage(channel="C08NTG6CXL5", text=f"No data for {sensor_display_name}")
            return

        current_start = times[0]
        # Max gap to consider a segment continuous (e.g., 5 minutes)
        max_gap_seconds = 5 * 60

        for i in range(1, len(times)):
            if (times[i] - times[i - 1]).total_seconds() > max_gap_seconds:
                timeline_segments.append((current_start, times[i - 1]))
                current_start = times[i]
        timeline_segments.append((current_start, times[-1]))  # Add the last segment

        fig, ax = plt.subplots(figsize=(12, 2))  # Wider for better time display
        ax.set_ylim(0.5, 1.5)  # Give some padding around the line

        for start, end in timeline_segments:
            # Plot each segment as a horizontal line
            ax.plot([start, end], [1, 1], color='blue', linewidth=10)

        ax.set_yticks([])  # No y-axis ticks needed for a timeline
        ax.set_title(f"Data Availability Timeline: {sensor_display_name} (Past 24h)")
        ax.set_xlabel("Time (UTC)")

        # Improve x-axis formatting
        import matplotlib.dates as mdates
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M\n%Y-%m-%d', tz=pytz.UTC))
        fig.autofmt_xdate()  # Rotate and align date labels

        plt.tight_layout()
        plt.savefig("sensor_timeline.png")
        plt.close()

        toronto_tz = pytz.timezone("America/Toronto")
        md_segments_table = "| Start (UTC)    | End (UTC)      | Start (Toronto) | End (Toronto)   |\n"
        md_segments_table += "|----------------|----------------|-----------------|-----------------|\n"

        for s, e in timeline_segments:
            s_utc_str = s.strftime('%Y-%m-%d %H:%M')
            e_utc_str = e.strftime('%Y-%m-%d %H:%M')
            s_local_str = s.astimezone(toronto_tz).strftime('%Y-%m-%d %H:%M')
            e_local_str = e.astimezone(toronto_tz).strftime('%Y-%m-%d %H:%M')
            md_segments_table += f"| {s_utc_str}Z | {e_utc_str}Z | {s_local_str}L | {e_local_str}L |\n"

        web_client.files_upload_v2(
            channel="C08NTG6CXL5",
            file="sensor_timeline.png",
            filename=f"{sensor_display_name}_timeline.png",
            title=f"{sensor_display_name} Data Timeline (24h)",
            initial_comment=(
                f"{default_sensor_message}📈 <@{user}> Data availability timeline for `{sensor_display_name}` (past 24h):\n"
                f"Each blue bar represents a period of continuous data (gaps > {max_gap_seconds // 60} mins start new bar).\n"
                f"```md\n{md_segments_table}\n```"
            )
        )

    except Exception as e:
        print(f"Error generating timeline for {sensor_display_name}:", e)
        import traceback
        traceback.print_exc()
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"❌ <@{user}> Failed to generate timeline for `{sensor_display_name}`. Error: {e}"
        )


def handle_help(user):
    help_message = (
        f"📘 <@{user}> Available Commands:\n"
        "```\n"
        "!location                     - Show the current car location on the map\n"
        "!testimage                   - Upload a test image\n"
        "!sensors                     - List all unique sensors detected in the past 24h\n"
        "!sensor plot NAME SECONDS [--d]\n"
        "                             - Plot sensor data for the last N seconds.\n"
        "                               Add --d to download raw data as CSV instead.\n"
        "!sensor plot NAME range START END [--d]\n"
        "                             - Plot sensor data between specified timestamps.\n"
        "                               START/END format: YYYYMMDDHHMMZ (UTC) or YYYYMMDDHHMML (Local).\n"
        "                               Add --d to download raw data as CSV instead.\n"
        "!sensor timeline [NAME]      - Show data availability timeline in the past 24h.\n"
        "                               Defaults to INV_DC_Bus_Voltage if NAME is omitted.\n"
        "!help                        - Show this help message\n\n"
        "Notes:\n"
        "  Z suffix for time = UTC (e.g., 202405201430Z)\n"
        "  L suffix for time = Local/Toronto (e.g., 202405201030L)\n"
        "  --d flag can be used with sensor plot commands for CSV download.\n"
        "```"
    )
    web_client.chat_postMessage(channel="C08NTG6CXL5", text=help_message)


def process_events(client: SocketModeClient, req: SocketModeRequest):
    if req.type == "events_api":
        # Acknowledge the event first to prevent retries from Slack
        client.send_socket_mode_response(
            SocketModeResponse(envelope_id=req.envelope_id)
        )

        event = req.payload.get("event", {})
        if (
                event.get("type") == "message"
                and event.get("subtype") is None  # Ignore bot messages, edits, etc.
                and event.get("channel") == "C08NTG6CXL5"  # Process messages only from this channel
        ):
            msg_ts = event.get("ts")
            if msg_ts in processed_messages:
                print(f"Skipping already processed message: {msg_ts}")
                return  # Already processed this message
            processed_messages.add(msg_ts)
            # Optional: Clean up old message timestamps to prevent memory growth
            if len(processed_messages) > 1000:  # Example limit
                oldest_ts = sorted(list(processed_messages))[0]
                processed_messages.remove(oldest_ts)

            user = event.get("user")
            if user == "U08P8KS8K25":  # Replace with your bot's actual User ID if different
                print("Skipping message from bot itself.")
                return

            text = event.get("text", "").strip()
            print(f"Received message from user {user} in channel {event.get('channel')}: \"{text}\"")

            if not text.startswith("!"):
                return  # Not a command

            command_text = text[1:] # Remove '!'

            # Pass the original casing of `text` to handlers if needed, or parts of it
            # For commands that are case-sensitive or where flag casing matters.
            # Here, for --d, we handle it by lowercasing original_text.
            # For sensor names, they are used as-is from `parts`.

            if command_text.startswith("location"):
                handle_location(user, client)  # client is socket_client here
            elif command_text.startswith("testimage"):
                handle_testimage(user)
            # Order matters: check for more specific "sensor plot range" before "sensor plot"
            elif command_text.startswith("sensor plot") and "range" in command_text:
                handle_sensor_plot_range(user, text[1:])  # Pass text without '!'
            elif command_text.startswith("sensor plot"):
                handle_sensor_plot(user, text[1:])  # Pass text without '!'
            elif command_text.startswith("sensors"):
                handle_sensors(user)
            elif command_text.startswith("sensor timeline"):
                handle_sensor_timeline(user, text[1:])  # Pass text without '!'
            elif command_text.startswith("help"):
                handle_help(user)
            # else:
            #     web_client.chat_postMessage(channel="C08NTG6CXL5", text=f"❓ <@{user}> Unknown command: `{text}`. Try `!help`.")


# Register the listener
socket_client.socket_mode_request_listeners.append(process_events)

# Connect and block forever
if __name__ == "__main__":
    print("🟢 Bot attempting to connect...")
    try:
        socket_client.connect()
        print("🟢 Bot connected and listening for messages.")
        Event().wait()  # Keep the main thread alive
    except Exception as e:
        print(f"🔴 Bot failed to connect: {e}")
        import traceback

        traceback.print_exc()