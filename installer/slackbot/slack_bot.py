import os
import requests
import csv
from io import StringIO
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import datetime
import pytz

# Removed load_dotenv() - using Docker Compose environment variables instead
from slack_sdk.web import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from threading import Event

processed_messages = set()

# --- Slack App Configuration ---
app_token = os.environ["SLACK_APP_TOKEN"]
bot_token = os.environ["SLACK_BOT_TOKEN"]

web_client = WebClient(token=bot_token)
socket_client = SocketModeClient(app_token=app_token, web_client=web_client)

WEBHOOK_URL = "https://hooks.slack.com/services/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"

# --- InfluxDB Configuration ---
INFLUX_URL = "http://influxwfr:8086"
INFLUX_ORG = "WFR"
INFLUX_BUCKET = "WFR2025"
# Consider moving INFLUX_TOKEN to an environment variable for security
INFLUX_TOKEN = "s9XkBC7pKOlb92-N9M40qilmxxoBe4wrnki4zpS_o0QSVTuMSQRQBerQB9Zv0YV40tmYayuX3w4G2MNizdy3qw=="
TORONTO_TZ = pytz.timezone("America/Toronto")

# --- Run Definition & Caching Configuration ---
DEFAULT_RUN_DEFINING_SENSOR = "INV_DC_Bus_Voltage"
RUN_HASH_CACHE = {}  # Cache: {hash_str: {"start_utc": datetime, "end_utc": datetime, "duration": timedelta}}

# --- Utility Functions ---
def generate_run_hash(dt_object_utc):
    """Generates a unique hash for a run based on its UTC start time."""
    return dt_object_utc.strftime('%y%m%d-%H%M%S')


def format_duration(timedelta_obj):
    """Formats a timedelta object into a human-readable string Hh Mm Ss."""
    total_seconds = int(timedelta_obj.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts) if parts else "0s"


def get_runs(time_range_str="24h", gap_seconds=60):
    """
    Identifies data runs based on DEFAULT_RUN_DEFINING_SENSOR activity
    and populates/updates the RUN_HASH_CACHE.
    A run is a continuous period of data collection from this sensor.
    A new run starts if there's no data for more than `gap_seconds`.
    """
    runs_for_return = []  # List to be returned for immediate use (e.g., by !list_runs)
    headers = {
        "Authorization": f"Token {INFLUX_TOKEN}",
        "Content-Type": "application/vnd.flux",
        "Accept": "application/csv"
    }
    flux_query = f'''
    from(bucket: "{INFLUX_BUCKET}")
        |> range(start: -{time_range_str})
        |> filter(fn: (r) => r["_measurement"] == "canBus" and
                              r["signalName"] == "{DEFAULT_RUN_DEFINING_SENSOR}" and
                              r["_field"] == "sensorReading")
        |> keep(columns: ["_time"])
        |> sort(columns: ["_time"], desc: false)
    '''
    try:
        response = requests.post(
            f"{INFLUX_URL}/api/v2/query?org={INFLUX_ORG}",
            headers=headers,
            data=flux_query
        )
        response.raise_for_status()
        csv_data = response.text
        f = StringIO(csv_data)
        reader = csv.DictReader(f)
        timestamps_utc = []
        for row in reader:
            if "_time" in row:
                try:
                    timestamps_utc.append(datetime.datetime.fromisoformat(row["_time"].replace("Z", "+00:00")))
                except ValueError:
                    print(f"Skipping invalid time format in get_runs: {row['_time']}")
                    continue

        if not timestamps_utc:
            print(f"No timestamps found for {DEFAULT_RUN_DEFINING_SENSOR} in get_runs for range {time_range_str}")
            return []

        timestamps_utc.sort()  # Should be sorted by query, but defensive sort

        current_run_start_utc = timestamps_utc[0]
        for i in range(1, len(timestamps_utc)):
            time_diff = (timestamps_utc[i] - timestamps_utc[i - 1]).total_seconds()
            if time_diff > gap_seconds:
                current_run_end_utc = timestamps_utc[i - 1]
                run_hash = generate_run_hash(current_run_start_utc)
                run_details = {
                    "start_utc": current_run_start_utc,
                    "end_utc": current_run_end_utc,
                    "duration": current_run_end_utc - current_run_start_utc
                }
                RUN_HASH_CACHE[run_hash] = run_details  # Update global cache
                runs_for_return.append({"hash": run_hash, **run_details})
                current_run_start_utc = timestamps_utc[i]

        # Add the last run
        if timestamps_utc:  # Ensure there was at least one timestamp
            final_run_end_utc = timestamps_utc[-1]
            run_hash = generate_run_hash(current_run_start_utc)
            run_details = {
                "start_utc": current_run_start_utc,
                "end_utc": final_run_end_utc,
                "duration": final_run_end_utc - current_run_start_utc
            }
            RUN_HASH_CACHE[run_hash] = run_details  # Update global cache
            runs_for_return.append({"hash": run_hash, **run_details})

        return runs_for_return

    except Exception as e:
        print(f"Error in get_runs (querying {DEFAULT_RUN_DEFINING_SENSOR}): {e}")
        import traceback
        traceback.print_exc()
        return []


# --- Core Plotting/Downloading Function ---
def _plot_or_download_sensor_data_for_range(user, web_client_instance, sensor_name, start_dt_utc, end_dt_utc,
                                            download_requested, title_suffix, filename_tag_suffix):
    influx_bucket = INFLUX_BUCKET
    start_iso = start_dt_utc.isoformat()
    end_iso = end_dt_utc.isoformat()

    if download_requested:
        raw_flux_query = f'''
        from(bucket: "{influx_bucket}")
            |> range(start: {start_iso}, stop: {end_iso})
            |> filter(fn: (r) => r["_measurement"] == "canBus" and r["signalName"] == "{sensor_name}" and r["_field"] == "sensorReading")
            |> sort(columns: ["_time"])
            |> yield(name: "raw")
        '''
        download_raw_sensor_data(user, web_client_instance, sensor_name, raw_flux_query, filename_tag_suffix)
        return

    try:
        aggregation_window = "1s"
        duration_seconds = (end_dt_utc - start_dt_utc).total_seconds()
        if duration_seconds > 3 * 3600:
            aggregation_window = "10s"
        if duration_seconds > 24 * 3600:
            aggregation_window = "1m"

        plot_flux_query = f'''
        from(bucket: "{influx_bucket}")
            |> range(start: {start_iso}, stop: {end_iso})
            |> filter(fn: (r) => r["_measurement"] == "canBus" and r["signalName"] == "{sensor_name}" and r["_field"] == "sensorReading")
            |> aggregateWindow(every: {aggregation_window}, fn: mean, createEmpty: false)
            |> yield(name: "mean")
        '''
        headers = {
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv"
        }
        response = requests.post(f"{INFLUX_URL}/api/v2/query?org={INFLUX_ORG}", headers=headers, data=plot_flux_query)
        response.raise_for_status()
        csv_data = response.text
        f = StringIO(csv_data)
        reader = csv.DictReader(f)
        times, values = [], []
        for row in reader:
            if "_time" in row and "_value" in row and row["_value"]:
                try:
                    times.append(datetime.datetime.fromisoformat(row["_time"].replace("Z", "+00:00")))
                    values.append(float(row["_value"]))
                except ValueError:
                    print(f"Skipping row with invalid data for plot: {row}")
                    continue

        if not times or not values:
            web_client_instance.chat_postMessage(
                channel="C08NTG6CXL5",
                text=f"⚠️ <@{user}> No data found for `{sensor_name}` for {title_suffix} to plot."
            )
            return

        plt.figure(figsize=(10, 4))
        plt.plot(times, values, marker='o', linestyle='-')
        plt.title(f"Sensor: {sensor_name} ({title_suffix}, {aggregation_window} Mean)")
        plt.xlabel("Time (UTC)")
        plt.ylabel(f"Value ({sensor_name})")
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M:%S', tz=pytz.UTC))
        plt.gcf().autofmt_xdate()
        plt.grid(True)
        plt.tight_layout()
        plot_filename = f"sensor_plot_{sensor_name.replace('/', '_')}_{filename_tag_suffix.replace(':', '').replace('-', '')}.png"
        plt.savefig(plot_filename)
        plt.close()

        # Upload and then remove the local file
        web_client_instance.files_upload_v2(
            channel="C08NTG6CXL5",
            file=plot_filename,
            filename=plot_filename,
            title=f"{sensor_name} - {title_suffix}",
            initial_comment=f"📊 <@{user}> Plot for `{sensor_name}` ({title_suffix}):"
        )
        try:
            os.remove(plot_filename)
        except OSError as e:
            print(f"Error removing plot file {plot_filename}: {e}")

    except Exception as e:
        print(f"Error plotting sensor {sensor_name} for {title_suffix}:", e)
        web_client_instance.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"❌ <@{user}> Failed to generate plot for `{sensor_name}` ({title_suffix}). Error: {e}"
        )


# --- Slack Command Handlers ---
def handle_location(user, client):
    try:
        r = requests.get("http://lappy-server:8050/api/track?type=location")
        r.raise_for_status()
        loc = r.json().get("location", {})
        lat, lon = loc.get("lat"), loc.get("lon")
        map_url = f"https://www.google.com/maps/@{lat},{lon},17z"  # Standard Google Maps URL
        client.web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"📍 <@{user}> Current :daqcar: location:\n<{map_url}|View on Map>\nLatitude: {lat}\nLongitude: {lon}"
        )
    except Exception as e:
        print("Error fetching location:", e)
        client.web_client.chat_postMessage(
            channel="C08NTG6CXL5", text=f"❌ <@{user}> Failed to retrieve car location. Error: {e}"
        )


def handle_testimage(user):
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
        web_client.chat_postMessage(channel="C08NTG6CXL5", text=f"❌ <@{user}> Failed to upload image. Error: {e}")


def handle_sensors(user):
    try:
        flux_query = f'''
        from(bucket: "{INFLUX_BUCKET}") |> range(start: -1d) 
            |> filter(fn: (r) => r["_measurement"] == "canBus") |> distinct(column: "signalName")
        '''
        headers = {
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv"
        }
        response = requests.post(f"{INFLUX_URL}/api/v2/query?org={INFLUX_ORG}", headers=headers, data=flux_query)
        response.raise_for_status()
        csv_data = response.text
        f = StringIO(csv_data)
        reader = csv.DictReader(f)
        signal_names = [row["_value"] for row in reader if "_value" in row]
        if signal_names:
            sensor_list = "\n".join(f"- `{s}`" for s in signal_names)
            web_client.chat_postMessage(
                channel="C08NTG6CXL5",
                text=f"🧪 <@{user}> Unique sensors in past day:\n{sensor_list}"
            )
        else:
            web_client.chat_postMessage(
                channel="C08NTG6CXL5",
                text=f"⚠️ <@{user}> No sensors found in the past day."
            )
    except Exception as e:
        print("Error fetching sensors:", e)
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"❌ <@{user}> Failed to fetch sensors. Error: {e}"
        )


def download_raw_sensor_data(user, web_client_instance, sensor_name, flux_query_raw, filename_suffix_tag):
    try:
        headers = {
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv"
        }
        print(f"Executing raw data query for {sensor_name}:\n{flux_query_raw}")
        response = requests.post(f"{INFLUX_URL}/api/v2/query?org={INFLUX_ORG}", headers=headers, data=flux_query_raw)
        response.raise_for_status()
        csv_content = response.text
        if not csv_content.strip() or len(csv_content.splitlines()) < 5:  # Basic check for empty data
            web_client_instance.chat_postMessage(
                channel="C08NTG6CXL5",
                text=f"⚠️ <@{user}> No raw data for `{sensor_name}` (suffix `{filename_suffix_tag}`)."
            )
            return
        csv_filename = f"raw_data_{sensor_name.replace('/', '_')}_{filename_suffix_tag.replace(':', '').replace('-', '')}.csv"
        web_client_instance.files_upload_v2(
            channel="C08NTG6CXL5",
            content=csv_content.encode('utf-8'),
            filename=csv_filename,
            title=f"Raw data: {sensor_name} ({filename_suffix_tag})",
            initial_comment=f"📄 <@{user}> Raw CSV data for `{sensor_name}`:"
        )
    except Exception as e:
        print(f"Error downloading raw sensor data for {sensor_name}:", e)
        web_client_instance.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"❌ <@{user}> Failed to download raw CSV for `{sensor_name}`. Error: {e}"
        )


def handle_sensor_plot(user, text):  # text is command_full
    original_text = text
    download_requested = "-d" in original_text
    if download_requested:
        text = original_text.replace("-d", "").strip()
    parts = text.strip().split()
    if len(parts) != 4 or parts[0] != "sensor" or parts[1] != "plot":
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Usage: `sensor plot SENSORNAME SECONDS [-d]`"
        )
        return
    sensor_name, seconds_str = parts[2], parts[3]
    try:
        seconds = int(seconds_str)
        if seconds <= 0:
            raise ValueError("Seconds must be positive.")
    except ValueError:
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Invalid time: `{seconds_str}`. Must be positive int."
        )
        return
    end_dt_utc = datetime.datetime.now(pytz.UTC)
    start_dt_utc = end_dt_utc - datetime.timedelta(seconds=seconds)
    _plot_or_download_sensor_data_for_range(
        user,
        web_client,
        sensor_name,
        start_dt_utc,
        end_dt_utc,
        download_requested,
        f"Last {seconds}s",
        f"last_{seconds}s"
    )


def handle_sensor_plot_range(user, text):  # text is command_full
    original_text = text
    download_requested = "-d" in original_text
    if download_requested:
        text = original_text.replace("-d", "").strip()
    parts = text.strip().split()
    if len(parts) != 6 or parts[0] != "sensor" or parts[1] != "plot" or parts[3] != "range":
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Usage: `sensor plot SENSORNAME range START END [-d]` (Time: `YYYYMMDDHHMMZ` or `L`)"
        )
        return
    sensor_name, start_raw, end_raw = parts[2], parts[4], parts[5]

    def parse_time(s):
        tz = TORONTO_TZ if s.upper().endswith("L") else pytz.UTC
        base = s[:-1] if s.upper().endswith(("Z", "L")) else s
        return tz.localize(datetime.datetime.strptime(base, "%Y%m%d%H%M")).astimezone(pytz.UTC)

    try:
        start_dt_utc, end_dt_utc = parse_time(start_raw), parse_time(end_raw)
        if start_dt_utc >= end_dt_utc:
            web_client.chat_postMessage(
                channel="C08NTG6CXL5",
                text=f"⚠️ <@{user}> Start time must be before end time."
            )
            return
    except ValueError as e:
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Invalid time format. Use `YYYYMMDDHHMMZ` (UTC) or `L` (Local). Error: {e}"
        )
        return
    _plot_or_download_sensor_data_for_range(
        user,
        web_client,
        sensor_name,
        start_dt_utc,
        end_dt_utc,
        download_requested,
        f"{start_raw} to {end_raw}",
        f"{start_raw}_to_{end_raw}"
    )


def handle_list_runs(user, text):  # text is command_full
    parts = text.strip().split()
    time_range_str = "24h"  # Default
    if len(parts) == 2:
        time_range_str = parts[1]  # e.g. list_runs 7d
    elif len(parts) > 2:
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Usage: `!list_runs [PERIOD]` (e.g., `7d`, `24h`)"
        )
        return
    runs_data = get_runs(time_range_str)  # Populates cache and returns list
    if not runs_data:
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"🤷 <@{user}> No runs found for `{DEFAULT_RUN_DEFINING_SENSOR}` in last `{time_range_str}`."
        )
        return
    runs_data.sort(key=lambda r: r["start_utc"], reverse=True)
    message = (
        f"📊 <@{user}> Data Runs (last `{time_range_str}`, based on `{DEFAULT_RUN_DEFINING_SENSOR}` activity, gap > 60s):\n"
    )
    table_header = "| Hash         | Start (UTC)   | End (UTC)     | Start (Local) | End (Local)   | Duration   |\n"
    table_sep = "|--------------|---------------|---------------|---------------|---------------|------------|\n"
    md_table = table_header + table_sep
    display_limit = 20
    for i, run in enumerate(runs_data):
        if i >= display_limit:
            break
        s_utc = run["start_utc"].strftime('%y%m%d%H%M%S') + "Z"
        e_utc = run["end_utc"].strftime('%y%m%d%H%M%S') + "Z"
        s_local = run["start_utc"].astimezone(TORONTO_TZ).strftime('%y%m%d%H%M%S') + "L"
        e_local = run["end_utc"].astimezone(TORONTO_TZ).strftime('%y%m%d%H%M%S') + "L"
        md_table += (
            f"| `{run['hash']}` | {s_utc:<13} | {e_utc:<13} | "
            f"{s_local:<13} | {e_local:<13} | {format_duration(run['duration']):<10} |\n"
        )
    if len(runs_data) > display_limit:
        md_table += f"... and {len(runs_data) - display_limit} more runs.\n"
    web_client.chat_postMessage(
        channel="C08NTG6CXL5",
        text=f"{message}```md\n{md_table}```"
    )


def handle_sensor_plot_run(user, text):  # text is command_full
    original_text = text
    download_requested = "-d" in original_text
    text_for_parsing = original_text.replace("-d", "").strip() if download_requested else original_text.strip()
    parts = text_for_parsing.split()
    if not (len(parts) == 5 and parts[0] == "sensor" and parts[1] == "plot" and parts[3] == "run"):
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Usage: `sensor plot SENSORNAME run RUN_HASH [-d]`"
        )
        return
    sensor_name, run_hash_to_find = parts[2], parts[4]
    found_run_details = RUN_HASH_CACHE.get(run_hash_to_find)
    scan_performed = False
    if not found_run_details:
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=(
                f"ℹ️ <@{user}> Hash `{run_hash_to_find}` not in cache. "
                "Scanning up to 90 days of "
                f"`{DEFAULT_RUN_DEFINING_SENSOR}` activity... moment."
            )
        )
        print(f"Cache miss for run hash {run_hash_to_find}. Performing 90d scan to populate cache.")
        get_runs(time_range_str="90d")  # Side effect: populates RUN_HASH_CACHE
        scan_performed = True
        found_run_details = RUN_HASH_CACHE.get(run_hash_to_find)  # Check cache again

    if found_run_details:
        start_dt_utc = found_run_details["start_utc"]
        end_dt_utc = found_run_details["end_utc"]
        _plot_or_download_sensor_data_for_range(
            user,
            web_client,
            sensor_name,
            start_dt_utc,
            end_dt_utc,
            download_requested,
            f"Run {run_hash_to_find}",
            f"run_{run_hash_to_find}"
        )
    else:
        message = f"⚠️ <@{user}> Run hash `{run_hash_to_find}` not found."
        if scan_performed:
            message += " Searched up to 90 days of history."
        else:
            message += " Try `!list_runs`."
        web_client.chat_postMessage(channel="C08NTG6CXL5", text=message)


def handle_sensor_timeline(user, text):  # text is command_full
    parts = text.strip().split()
    sensor_name_filter = DEFAULT_RUN_DEFINING_SENSOR  # Default for timeline is now same as run definition
    default_sensor_message = f"⚠️ No sensor specified. Defaulting to `{sensor_name_filter}`.\n"
    if not (parts[0] == "sensor" and parts[1] == "timeline" and (len(parts) == 2 or len(parts) == 3)):
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"⚠️ <@{user}> Usage: `sensor timeline [SENSORNAME]`"
        )
        return
    if len(parts) == 3:
        sensor_name_filter = parts[2]
        default_sensor_message = ""
    sensor_display_name = sensor_name_filter
    try:
        flux_query = f'''
        from(bucket: "{INFLUX_BUCKET}") |> range(start: -24h)
            |> filter(fn: (r) => r["_measurement"] == "canBus" and r["signalName"] == "{sensor_name_filter}" and r["_field"] == "sensorReading")
            |> keep(columns: ["_time"]) |> sort(columns: ["_time"])
        '''
        headers = {
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv"
        }
        response = requests.post(f"{INFLUX_URL}/api/v2/query?org={INFLUX_ORG}", headers=headers, data=flux_query)
        response.raise_for_status()
        csv_data = response.text
        f = StringIO(csv_data)
        reader = csv.DictReader(f)
        times = [datetime.datetime.fromisoformat(row["_time"].replace("Z", "+00:00")) for row in reader if "_time" in row]
        if not times:
            web_client.chat_postMessage(
                channel="C08NTG6CXL5",
                text=(
                    f"{default_sensor_message}⚠️ <@{user}> No data for `{sensor_display_name}` "
                    "(past 24h) for timeline."
                )
            )
            return
        times.sort()
        timeline_segments = []
        current_start = times[0]
        max_gap_seconds = 5 * 60  # Visual grouping gap
        for i in range(1, len(times)):
            if (times[i] - times[i - 1]).total_seconds() > max_gap_seconds:
                timeline_segments.append((current_start, times[i - 1]))
                current_start = times[i]
        timeline_segments.append((current_start, times[-1]))
        fig, ax = plt.subplots(figsize=(12, 2))
        ax.set_ylim(0.5, 1.5)
        [ax.plot([s, e], [1, 1], color='blue', linewidth=10) for s, e in timeline_segments]
        ax.set_yticks([])
        ax.set_title(f"Data Availability: {sensor_display_name} (24h)")
        ax.set_xlabel("Time (UTC)")
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M\n%Y-%m-%d', tz=pytz.UTC))
        fig.autofmt_xdate()
        plt.tight_layout()
        timeline_fn = f"sensor_timeline_{sensor_display_name.replace('/', '_')}.png"
        plt.savefig(timeline_fn)
        plt.close()

        tbl = (
            "| Start (UTC) | End (UTC) | Start (Local) | End (Local) |\n"
            "|---|---|---|---|\n"
            + "\n".join([
                f"| {s.strftime('%y%m%d%H%M')}Z | {e.strftime('%y%m%d%H%M')}Z | "
                f"{s.astimezone(TORONTO_TZ).strftime('%y%m%d%H%M')}L | "
                f"{e.astimezone(TORONTO_TZ).strftime('%y%m%d%H%M')}L |"
                for s, e in timeline_segments
            ])
        )

        web_client.files_upload_v2(
            channel="C08NTG6CXL5",
            file=timeline_fn,
            filename=timeline_fn,
            title=f"{sensor_display_name} Timeline (24h)",
            initial_comment=(
                f"{default_sensor_message}📈 <@{user}> Data availability for "
                f"`{sensor_display_name}` (gaps > {max_gap_seconds // 60}m start new bar/row):\n```md\n{tbl}\n```"
            )
        )
        try:
            os.remove(timeline_fn)
        except OSError as e:
            print(f"Error removing timeline file {timeline_fn}: {e}")

    except Exception as e:
        print(f"Error in sensor_timeline for {sensor_display_name}: {e}")
        import traceback
        traceback.print_exc()
        web_client.chat_postMessage(
            channel="C08NTG6CXL5",
            text=f"❌ <@{user}> Failed timeline for `{sensor_display_name}`. Error: {e}"
        )


def handle_help(user):
    help_text = (
        f"📘 <@{user}> Available Commands:\n"
        "```\n"
        "!location                     - Show current car location.\n"
        "!testimage                    - Upload a test image.\n"
        "!sensors                      - List unique sensors (past 24h).\n"
        "\n"
        f"!list_runs [PERIOD]           - List data runs based on '{DEFAULT_RUN_DEFINING_SENSOR}' activity.\n"
        "                                PERIOD e.g., 7d, 24h (default).\n"
        "                                A run is continuous data (gap > 60s = new run).\n"
        "\n"
        "!sensor plot NAME SECONDS [-d]\n"
        "                              - Plot sensor for last N seconds.\n"
        "                                -d: download raw CSV instead.\n"
        "!sensor plot NAME range START END [-d]\n"
        "                              - Plot sensor for UTC/Local time range.\n"
        "                                Time: YYYYMMDDHHMMZ (UTC) or YYYYMMDDHHMML (Local).\n"
        "!sensor plot NAME run HASH [-d]\n"
        "                              - Plot sensor for a specific run HASH.\n"
        "                                Get HASH from '!list_runs'. Cache used for speed.\n"
        "\n"
        "!sensor timeline [NAME]       - Show data availability timeline (past 24h).\n"
        f"                               Default: {DEFAULT_RUN_DEFINING_SENSOR} if NAME omitted.\n"
        "\n"
        "!help                         - Show this help message.\n"
        "```"
    )
    web_client.chat_postMessage(channel="C08NTG6CXL5", text=help_text)


# --- Event Processing Logic ---
def process_events(client: SocketModeClient, req: SocketModeRequest):
    if req.type == "events_api":
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        event = req.payload.get("event", {})
        if (event.get("type") == "message" and event.get("subtype") is None and
                event.get("channel") == "C08NTG6CXL5"):
            msg_ts = event.get("ts")
            if msg_ts in processed_messages:
                print(f"Skipping already processed message: {msg_ts}")
                return
            processed_messages.add(msg_ts)
            if len(processed_messages) > 1000:
                oldest_ts = sorted(list(processed_messages))[0]
                processed_messages.remove(oldest_ts)

            user = event.get("user")
            bot_user_id = os.environ.get("SLACK_BOT_USER_ID", "U08P8KS8K25")  # Replace with your bot's actual User ID
            if user == bot_user_id:
                print(f"Skipping message from bot itself ({bot_user_id}).")
                return

            text = event.get("text", "").strip()
            if not text.startswith("!"):
                return

            command_full = text[1:]
            command_parts = command_full.split()
            main_command = command_parts[0] if command_parts else ""

            print(f"Received command: '{command_full}' from user {user} in channel {event.get('channel')}")

            if main_command == "location":
                handle_location(user, client)
            elif main_command == "testimage":
                handle_testimage(user)
            elif main_command == "sensors":
                handle_sensors(user)
            elif main_command == "list_runs":
                handle_list_runs(user, command_full)
            elif main_command == "sensor":
                if len(command_parts) > 1:
                    sub_command = command_parts[1]
                    if sub_command == "plot":
                        if "run" in command_parts:
                            handle_sensor_plot_run(user, command_full)
                        elif "range" in command_parts:
                            handle_sensor_plot_range(user, command_full)
                        else:
                            handle_sensor_plot(user, command_full)
                    elif sub_command == "timeline":
                        handle_sensor_timeline(user, command_full)
                    else:
                        web_client.chat_postMessage(
                            channel="C08NTG6CXL5",
                            text=f"❓ <@{user}> Unknown 'sensor' subcommand. Try `!help`."
                        )
                else:
                    web_client.chat_postMessage(
                        channel="C08NTG6CXL5",
                        text=f"❓ <@{user}> Incomplete 'sensor' command. Try `!help`."
                    )
            elif main_command == "help":
                handle_help(user)
            else:
                web_client.chat_postMessage(
                    channel="C08NTG6CXL5",
                    text=f"❓ <@{user}> Unknown command: `{text}`. Try `!help`."
                )


# --- Main Execution ---
if __name__ == "__main__":
    print("🟢 Bot attempting to connect...")
    # Optional: Retrieve bot_user_id dynamically if not set as ENV var
    # try:
    #     auth_response = web_client.auth_test()
    #     os.environ["SLACK_BOT_USER_ID"] = auth_response["user_id"]
    #     print(f"🤖 Bot User ID set to: {os.environ['SLACK_BOT_USER_ID']}")
    # except Exception as e:
    #     print(f"⚠️ Could not dynamically set SLACK_BOT_USER_ID: {e}. Ensure it's set in .env or environment.")

    socket_client.socket_mode_request_listeners.append(process_events)
    try:
        socket_client.connect()
        requests.post(
            "https://hooks.slack.com/services/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",
            json={"text": "Lappy on duty! :lappy:"}
        )
        print("🟢 Bot connected and listening for messages.")
        Event().wait()
    except Exception as e:
        print(f"🔴 Bot failed to connect: {e}")
        import traceback
        traceback.print_exc()