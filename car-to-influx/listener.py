#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask, request, jsonify, render_template
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import WriteOptions
from datetime import datetime, timezone, timedelta
import cantools, os, logging
import requests
from collections import deque
import threading

# ─── CONFIG ────────────────────────────────────────────────────────────────
INFLUX_URL = "http://3.98.181.12:8086"
INFLUX_TOKEN = "s9XkBC7pKOlb92-N9M40qilmxxoBe4wrnki4zpS_o0QSVTuMSQRQBerQB9Zv0YV40tmYayuX3w4G2MNizdy3qw=="
INFLUX_ORG = "WFR"
INFLUX_BUCKET = "LTEtest"  # Ensure this is the target bucket
DBC_FILE = "WFR25-f772b40.dbc"  # Ensure this DBC matches incoming CAN IDs
PORT = int(os.getenv("PORT", "8085"))
WEBHOOK_URL = "https://hooks.slack.com/services/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"
WEBHOOK_MESSAGE_INTERVAL = timedelta(minutes=1)

# ─── RELATIVE‐TIMESTAMP ANCHORING ─────────────────────────────────────────
_reset_threshold = timedelta(seconds=60)
_first_relative = True
_relative_anchor_ts = 0.0
_relative_anchor_real = datetime.now(timezone.utc)
_last_raw_ts = 0.0

# ─── WEBHOOK STATE ─────────────────────────────────────────────────────────
_last_successful_receipt_time = None

# ─── PACKET STATISTICS FOR GUI ─────────────────────────────────────────────
packet_history = deque()
history_lock = threading.Lock()
MAX_HISTORY_SECONDS = 75

# ─── SIGNAL DEFINITION CACHE (Inspired by CSV script) ────────────────────
# Cache for cantools signal definition objects to reduce DBC lookups
# Key: (message_name_str, signal_name_str), Value: signal_definition_object
signal_definition_cache = {}
# Lock for thread-safe access to the signal_definition_cache
signal_cache_lock = threading.Lock()

# ─── LOGGER SETUP ──────────────────────────────────────────────────────────
app = Flask(__name__)
# Configure logging
file_handler = logging.FileHandler("listener.log")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s: %(message)s [%(module)s:%(lineno)d in %(funcName)s]", datefmt="%Y-%m-%d %H:%M:%S"))

# Add handler to Flask's app.logger
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)  # Set Flask's logger to INFO

# Also configure the root logger if you want general Flask/Werkzeug logs to go to the file too
# logging.basicConfig(handlers=[file_handler], level=logging.INFO,
#                     format='%(asctime)s %(levelname)s: %(message)s [%(name)s:%(lineno)d in %(funcName)s]',
#                     datefmt='%Y-%m-%d %H:%M:%S')


# ─── LOAD DBC & INFLUX CLIENT ─────────────────────────────────────
try:
    db = cantools.database.load_file(DBC_FILE)
    app.logger.info(f"Successfully loaded DBC: {DBC_FILE}")
except Exception as e:
    app.logger.critical(f"Failed to load DBC file: {DBC_FILE} - {e}")
    raise SystemExit(f"Failed to load DBC file: {e}")

client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
write_api = client.write_api(write_options=WriteOptions(batch_size=500, flush_interval=1000))


def _bytes_from_field(data_field):
    """
    Converts various data field formats into a bytes object.
    Handles lists of integers, or a space-separated string of hex/decimal numbers.
    """
    if isinstance(data_field, list):
        # Ensure all elements are integers before conversion
        return bytes(int(b) & 0xFF for b in data_field)
    if isinstance(data_field, str):
        # Split string and convert each part, handling potential "0x" prefix
        return bytes(int(b, 16 if b.lower().startswith("0x") else 10) & 0xFF
                     for b in data_field.split())
    if data_field is None:
        return b''  # Return empty bytes if data is None
    raise ValueError(f"Unrecognized data format for _bytes_from_field: {type(data_field)} {data_field!r}")


def _ts_to_datetime(ts: float) -> datetime:
    """
    Converts a timestamp (potentially relative) to a UTC datetime object.
    Anchors relative timestamps to the current server time on first receipt or reset.
    """
    global _first_relative, _relative_anchor_ts, _relative_anchor_real, _last_raw_ts
    # If timestamp is likely a full Unix timestamp (seconds since epoch)
    if ts > 946_684_800:  # Approx 2000-01-01 in seconds
        return datetime.fromtimestamp(ts, timezone.utc)

    # Handle relative timestamps
    current_time = datetime.now(timezone.utc)
    if _first_relative:
        _relative_anchor_real = current_time
        _relative_anchor_ts = ts
        _last_raw_ts = ts
        _first_relative = False
        app.logger.info(
            f"Anchoring relative timestamp: real={_relative_anchor_real.isoformat()}, device_ts={_relative_anchor_ts}")
        return _relative_anchor_real

    # Detect reset: if current relative ts is much smaller than last, and difference is beyond threshold
    if ts < _last_raw_ts and (_last_raw_ts - ts) > _reset_threshold.total_seconds():
        app.logger.info(f"Relative timestamp reset detected: old_ts={_last_raw_ts}, new_ts={ts}. Re-anchoring.")
        _relative_anchor_real = current_time
        _relative_anchor_ts = ts

    _last_raw_ts = ts
    elapsed_seconds = ts - _relative_anchor_ts
    # Prevent negative timedelta if ts is slightly less than anchor due to clock drift or reordering
    if elapsed_seconds < 0:
        app.logger.warning(
            f"Negative elapsed time ({elapsed_seconds}s) for relative ts {ts} against anchor {_relative_anchor_ts}. Using current time.")
        # If the device clock seems to have jumped back significantly beyond anchor, re-anchor
        if abs(elapsed_seconds) > _reset_threshold.total_seconds() / 2:  # Heuristic for re-anchor
            _relative_anchor_real = current_time
            _relative_anchor_ts = ts
            app.logger.info(
                f"Re-anchoring due to significant negative jump: real={_relative_anchor_real.isoformat()}, device_ts={_relative_anchor_ts}")
            return _relative_anchor_real
        return _relative_anchor_real  # Default to anchor time if small negative delta

    elapsed = timedelta(seconds=elapsed_seconds)
    return _relative_anchor_real + elapsed


def send_webhook_notification():
    """Sends a notification to the configured webhook URL."""
    try:
        payload = {"text": "CAN data listener: No data received for a while. Please check the source."}
        response = requests.post(WEBHOOK_URL, json=payload, timeout=10)  # 10 second timeout
        response.raise_for_status()  # Raises an HTTPError for bad responses (4XX or 5XX)
        app.logger.info("Webhook notification sent successfully.")
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Webhook notification failed: {e}")


@app.route("/can", methods=["POST"])
def ingest_can():
    global _last_successful_receipt_time, packet_history, history_lock, signal_definition_cache, signal_cache_lock

    current_server_time = datetime.now(timezone.utc)

    try:
        # force=True will attempt to parse JSON even if mimetype isn't application/json
        payload = request.get_json(force=True)
    except Exception as e:  # Catches Werkzeug's BadRequest if JSON is malformed
        app.logger.warning(f"Invalid JSON payload received from {request.remote_addr}: {e}")
        return jsonify(error=f"Invalid JSON: {str(e)}"), 400

    # Expecting a list of frames, or a dict with a "messages" key containing the list
    frames = payload.get("messages") if isinstance(payload, dict) else payload
    if not isinstance(frames, list) or not frames:  # Ensure frames is a non-empty list
        app.logger.warning(
            f"Expected non-empty JSON array or 'messages' list from {request.remote_addr}. Payload: {payload}")
        return jsonify(error="Expected non-empty JSON array or object with 'messages' list"), 400

    # Webhook logic: if it's been too long since last successful receipt, send notification
    if _last_successful_receipt_time:
        if (current_server_time - _last_successful_receipt_time) > WEBHOOK_MESSAGE_INTERVAL:
            app.logger.info(
                f"Data receipt gap detected ({(current_server_time - _last_successful_receipt_time).total_seconds()}s). Sending webhook notification.")
            send_webhook_notification()
    elif not frames:  # No initial data yet, but received a (possibly empty) request
        app.logger.info("Initial request received, but no frames to process. Webhook timer not started.")

    if frames:  # Only update if we actually have frames to process
        _last_successful_receipt_time = current_server_time

    # Update packet history for GUI status
    num_received_frames_in_batch = len(frames)
    total_data_size_in_batch = 0
    for frame_content_for_stats in frames:
        data_raw_for_stats = frame_content_for_stats.get("data")
        try:
            total_data_size_in_batch += len(_bytes_from_field(data_raw_for_stats))
        except ValueError as e:
            app.logger.warning(f"Malformed 'data' field for stats calculation: {data_raw_for_stats}, error: {e}")
            # Continue, but this frame's data size won't be counted

    with history_lock:
        packet_history.append((current_server_time, num_received_frames_in_batch, total_data_size_in_batch))
        # Prune old entries from the deque
        cutoff_for_deque = current_server_time - timedelta(seconds=MAX_HISTORY_SECONDS)
        while packet_history and packet_history[0][0] < cutoff_for_deque:
            packet_history.popleft()

    app.logger.info(f"Received {len(frames)} frames for processing from {request.remote_addr}.")
    points = []  # List to hold InfluxDB Point objects

    for idx, frame in enumerate(frames):
        try:
            # --- Process CAN ID ---
            can_id_raw = frame.get("id")
            if can_id_raw is None:
                app.logger.warning(f"Frame #{idx + 1}: 'id' field missing. Skipping frame. Frame content: {frame}")
                continue

            if isinstance(can_id_raw, str):
                can_id = int(can_id_raw, 0)  # Auto-detect base (0x for hex, etc.)
            elif isinstance(can_id_raw, int):
                can_id = can_id_raw  # Already an int
            else:
                app.logger.warning(
                    f"Frame #{idx + 1}: 'id' field has unexpected type {type(can_id_raw)}. Value: {can_id_raw}. Skipping frame.")
                continue

            # --- Process Data ---
            data_raw = frame.get("data")  # data_raw can be list, string, or None
            data = _bytes_from_field(data_raw)  # Handles None to b'', list of ints, or string of bytes

            # --- Process Timestamp ---
            ts_raw_val = frame.get("timestamp")
            if ts_raw_val is None:
                app.logger.warning(
                    f"Frame #{idx + 1} (ID: {can_id:#x}): 'timestamp' field missing. Skipping frame. Frame content: {frame}")
                continue
            try:
                ts_raw = float(ts_raw_val)
            except (ValueError, TypeError) as e:
                app.logger.warning(
                    f"Frame #{idx + 1} (ID: {can_id:#x}): 'timestamp' field '{ts_raw_val}' is not a valid float. Error: {e}. Skipping frame.")
                continue
            ts_dt = _ts_to_datetime(ts_raw)

        except (ValueError, TypeError) as e:  # Catch errors from int(), float(), _bytes_from_field()
            app.logger.warning(
                f"Frame #{idx + 1}: Malformed basic field (id, data, or timestamp). Error: {e}. Frame: {frame}. Skipping frame.")
            continue
        except Exception as e:  # Catch any other unexpected errors during basic field processing
            app.logger.error(
                f"Frame #{idx + 1}: Unexpected error processing basic fields. Error: {e}. Frame: {frame}. Skipping frame.")
            continue

        # --- DBC Lookup and Decode ---
        try:
            msg = db.get_message_by_frame_id(can_id)
        except KeyError:  # Specific error if CAN ID not in DBC
            # Log less verbosely for unknown IDs if they are frequent, or add a counter
            app.logger.debug(
                f"Frame #{idx + 1}: Unknown CAN ID {can_id:#x} (decimal: {can_id}) in DBC {DBC_FILE}. Skipping frame.")
            continue
        except Exception as e:  # Other unexpected errors from cantools
            app.logger.warning(
                f"Frame #{idx + 1}: Error retrieving message for CAN ID {can_id:#x} from DBC. Error: {e}. Skipping frame.")
            continue

        try:
            # allow_truncated=True: useful if data length is less than defined in DBC
            # decode_choices=True: decodes enum values to their string representations
            decoded_signals = msg.decode(data, allow_truncated=True, decode_choices=True)
        except Exception as e:  # Catch errors from msg.decode()
            app.logger.warning(
                f"Frame #{idx + 1} (ID: {can_id:#x}, Name: {msg.name}): Decode error with data '{data.hex()}'. Error: {e}. Skipping frame.")
            continue

        # --- Process Decoded Signals ---
        for signal_name_from_decode, signal_value_obj in decoded_signals.items():
            signal_def = None
            cache_key = (msg.name, signal_name_from_decode)

            # Retrieve signal definition, using cache for performance
            with signal_cache_lock:
                if cache_key in signal_definition_cache:
                    signal_def = signal_definition_cache[cache_key]
                else:
                    try:
                        current_signal_def = msg.get_signal_by_name(signal_name_from_decode)
                        signal_definition_cache[cache_key] = current_signal_def
                        signal_def = current_signal_def
                    except Exception as e:  # e.g., if signal_name_from_decode is somehow not in msg
                        app.logger.warning(
                            f"Frame #{idx + 1} (Msg: {msg.name}, Signal: {signal_name_from_decode}): Could not get signal definition from DBC. Error: {e}. Skipping signal.")
                        # Do not add to cache if lookup failed.

            if not signal_def:  # If signal_def could not be retrieved
                continue

            # Extract signal properties
            description = signal_def.comment if signal_def.comment is not None else "No description"
            unit = signal_def.unit if signal_def.unit is not None else "N/A"
            actual_signal_name = signal_def.name  # Use name from signal_def for consistency

            sensor_val = None  # Numeric value of the signal
            signal_label = ""  # String representation (enum name or numeric value as string)

            # Handle different types of signal_value_obj from cantools
            # cantools can return NamedSignalValue (for enums) or raw numeric types
            if hasattr(signal_value_obj, 'value') and hasattr(signal_value_obj, 'name'):  # NamedSignalValue (enum)
                try:
                    sensor_val = float(signal_value_obj.value)  # The underlying numeric value of the enum
                    signal_label = str(signal_value_obj.name)  # The string name of the enum
                except (ValueError, TypeError) as e:
                    app.logger.warning(
                        f"Frame #{idx + 1} (Msg: {msg.name}, Signal: {actual_signal_name}): Error converting NamedSignalValue.value to float. Value: {signal_value_obj.value}. Error: {e}. Skipping signal.")
                    continue
            elif isinstance(signal_value_obj, (int, float)):  # Raw numeric value
                sensor_val = float(signal_value_obj)
                signal_label = str(signal_value_obj)  # For non-enum, label can be its string value
            else:  # Should not happen with decode_choices=True, but good to have a fallback
                app.logger.warning(
                    f"Frame #{idx + 1} (Msg: {msg.name}, Signal: {actual_signal_name}): Unhandled signal value type {type(signal_value_obj)}: {signal_value_obj!r}. Skipping signal.")
                continue

            # Create InfluxDB Point
            pt = (
                Point("canBus")  # Measurement name
                .tag("messageName", msg.name)
                .tag("signalName", actual_signal_name)
                .tag("canID", format(can_id, "#04x"))  # Format as hex e.g., 0xac
                .field("sensorReading", sensor_val)  # The numeric value
                .field("unit", unit)
                # .field("description", description) # Uncomment if description is needed in Influx
                .field("signalLabel", signal_label)  # Enum name or string of numeric value
                .time(ts_dt)  # Timestamp for the point
            )
            points.append(pt)

    # --- Write to InfluxDB ---
    if not points:
        app.logger.info(f"No points decoded from {len(frames)} received frames – nothing to write to InfluxDB.")
        # Return 200 OK even if no points, as the request itself was processed.
        return jsonify(status="no_points_decoded", received_frames=len(frames), written_points=0), 200

    try:
        write_api.write(bucket=INFLUX_BUCKET, record=points)
        app.logger.info(f"Successfully wrote {len(points)} points to InfluxDB bucket '{INFLUX_BUCKET}'.")
    except Exception as e:
        app.logger.error(f"InfluxDB write failed for {len(points)} points. Error: {e}")
        # Consider how to handle partial writes or retry mechanisms if necessary
        return jsonify(error=f"InfluxDB write failed: {str(e)}", written_points=0, received_frames=len(frames)), 500

    return jsonify(status="ok", written_points=len(points), received_frames=len(frames)), 201


@app.route("/")
def index():
    # This route serves the main HTML page for the GUI.
    # Ensure 'index.html' is in a 'templates' folder in the same directory as this script.
    return render_template("index.html")


@app.route("/status")
def status():
    """Provides a JSON status of the CAN listener for the GUI."""
    global _last_successful_receipt_time, packet_history, history_lock, signal_definition_cache
    now = datetime.now(timezone.utc)

    # Calculate stats for the last 60 seconds
    cutoff_time_60s = now - timedelta(seconds=60)
    packets_in_last_60s = 0
    size_in_last_60s_bytes = 0

    with history_lock:  # Access shared deque safely
        # Iterate over a copy of the deque for thread safety during iteration
        current_history_snapshot = list(packet_history)

    for ts_hist, num_frames_hist, total_size_hist in current_history_snapshot:
        if ts_hist > cutoff_time_60s:
            packets_in_last_60s += num_frames_hist
            size_in_last_60s_bytes += total_size_hist

    # Determine receiver status message
    receiver_status_message = "Initializing..."
    if _last_successful_receipt_time:
        time_since_last_receipt_seconds = (now - _last_successful_receipt_time).total_seconds()
        if time_since_last_receipt_seconds <= 10:  # Active if data received in the last 10 seconds
            receiver_status_message = f"Active (last data {time_since_last_receipt_seconds:.1f}s ago)"
        elif time_since_last_receipt_seconds <= MAX_HISTORY_SECONDS + 10:  # Tolerable delay
            receiver_status_message = f"Monitoring (last data {time_since_last_receipt_seconds:.0f}s ago)"
        else:  # Inactive for a while
            receiver_status_message = f"Inactive (last data {time_since_last_receipt_seconds:.0f}s ago)"
    else:
        receiver_status_message = "Awaiting Data (no messages received yet)"

    return jsonify({
        "receiver_status": receiver_status_message,
        "packets_last_60s": packets_in_last_60s,
        "data_rate_last_60s_bytes_sec": size_in_last_60s_bytes / 60 if packets_in_last_60s > 0 else 0,
        "size_last_60s_bytes": size_in_last_60s_bytes,
        "last_successful_receipt_time_iso": _last_successful_receipt_time.isoformat() if _last_successful_receipt_time else None,
        "current_server_time_iso": now.isoformat(),
        "signal_cache_size": len(signal_definition_cache),  # Diagnostic info
        "packet_history_size": len(packet_history)  # Diagnostic info
    })


if __name__ == "__main__":
    app.logger.info(f"Starting CAN ingest server on port {PORT}")
    app.logger.info(f"DBC File: {DBC_FILE} (Ensure this file contains definitions for expected CAN IDs)")
    app.logger.info(f"InfluxDB URL: {INFLUX_URL}, Org: {INFLUX_ORG}, Bucket: {INFLUX_BUCKET}")
    app.logger.info(f"Webhook notifications to Slack enabled. Interval: {WEBHOOK_MESSAGE_INTERVAL.total_seconds()}s")
    app.logger.info(f"Log file: listener.log")
    app.logger.info(f"GUI (if index.html is present) available at http://0.0.0.0:{PORT}/")

    # For production, use a proper WSGI server like Gunicorn or uWSGI
    # Example: gunicorn --workers 4 --bind 0.0.0.0:8085 listener:app
    app.run(host="0.0.0.0", port=PORT, debug=False)