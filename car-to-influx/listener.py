#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask, request, jsonify
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import WriteOptions
from datetime import datetime, timezone, timedelta
import cantools, os, logging
import requests  # Added for webhook

# ─── CONFIG ────────────────────────────────────────────────────────────────
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxwfr:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN",
                         "s9XkBC7pKOlb92-N9M40qilmxxoBe4wrnki4zpS_o0QSVTuMSQRQBerQB9Zv0YV40tmYayuX3w4G2MNizdy3qw==")
INFLUX_ORG = os.getenv("INFLUX_ORG", "WFR")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "ourCar")
DBC_FILE = os.getenv("DBC_FILE", "testing_data/20240129 Gen5 CAN DB.dbc")
PORT = int(os.getenv("PORT", "8085"))
WEBHOOK_URL = "https://hooks.slack.com/services/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"  # Hardcoded Webhook URL
WEBHOOK_MESSAGE_INTERVAL = timedelta(minutes=1)

# ─── RELATIVE‐TIMESTAMP ANCHORING ─────────────────────────────────────────
_reset_threshold = timedelta(seconds=60)
_first_relative = True
_relative_anchor_ts = 0.0
_relative_anchor_real = datetime.now(timezone.utc)
_last_raw_ts = 0.0

# ─── WEBHOOK STATE ─────────────────────────────────────────────────────────
_last_successful_receipt_time = None  # Tracks time of last valid message receipt

# ─── LOGGER SETUP ──────────────────────────────────────────────────────────
app = Flask(__name__)
file_handler = logging.FileHandler("listener.log")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s: %(message)s [%(funcName)s]", datefmt="%Y-%m-%d %H:%M:%S"))  # Added funcName
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.ERROR)  # Flask's logger level (console might be higher)

# ─── LOAD DBC & INFLUX CLIENT ─────────────────────────────────────────────
try:
    db = cantools.database.load_file(DBC_FILE)
    print(f"Loaded DBC: {DBC_FILE}")
except Exception as e:
    # Log to app logger as well if it's configured early enough, or just print/raise
    app.logger.critical(f"Failed to load DBC file: {DBC_FILE} - {e}")
    raise SystemExit(f"Failed to load DBC file: {e}")

client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
write_api = client.write_api(write_options=WriteOptions(batch_size=500, flush_interval=1000))


def _bytes_from_field(data_field):
    """Convert incoming 'data' (list[str|int] or str) into a bytes object."""
    if isinstance(data_field, list):
        # Ensure elements are converted to int before byte conversion if they are strings
        return bytes(int(b) & 0xFF for b in data_field)
    if isinstance(data_field, str):
        return bytes(int(b, 16 if b.lower().startswith("0x") else 10) & 0xFF
                     for b in data_field.split())
    raise ValueError(f"Unrecognized data format: {data_field!r}")


def _ts_to_datetime(ts: float) -> datetime:
    """
    Convert the incoming numeric timestamp to an aware UTC datetime.
    """
    global _first_relative, _relative_anchor_ts, _relative_anchor_real, _last_raw_ts

    if ts > 946_684_800:  # Roughly 2000-01-01
        return datetime.fromtimestamp(ts, timezone.utc)

    if _first_relative:
        _relative_anchor_real = datetime.now(timezone.utc)
        _relative_anchor_ts = ts
        _last_raw_ts = ts
        _first_relative = False
        # app.logger.info(f"Anchoring relative timestamps: anchor_real={_relative_anchor_real.isoformat()}, anchor_ts={_relative_anchor_ts}")
        return _relative_anchor_real

    if ts < _last_raw_ts and (_last_raw_ts - ts) > _reset_threshold.total_seconds():
        # app.logger.info(f"Re-anchoring relative timestamps due to reset: last_raw_ts={_last_raw_ts}, current_ts={ts}")
        _relative_anchor_real = datetime.now(timezone.utc)
        _relative_anchor_ts = ts

    _last_raw_ts = ts
    elapsed = timedelta(seconds=(ts - _relative_anchor_ts))
    return _relative_anchor_real + elapsed


def send_webhook_notification():
    """Sends a 'I hear the car whispering' notification via webhook."""
    try:
        payload = {"text": "I hear the car whispering"}
        response = requests.post(WEBHOOK_URL, json=payload, timeout=10)  # Timeout for the request
        response.raise_for_status()  # Raise an exception for HTTP error codes (4xx or 5xx)
        app.logger.info("Webhook notification sent successfully.")
    except requests.exceptions.Timeout:
        app.logger.error("Webhook notification failed: Timeout")
    except requests.exceptions.HTTPError as e:
        app.logger.error(f"Webhook notification failed: HTTP Error {e.response.status_code} - {e.response.text}")
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Webhook notification failed: {e}")


@app.route("/can", methods=["POST"])
def ingest_can():
    """Ingest JSON CAN frames, decode with DBC, and write to InfluxDB."""
    global _last_successful_receipt_time  # For updating the global variable

    current_server_time = datetime.now(timezone.utc)

    try:
        payload = request.get_json(force=True)
    except Exception as e:  # Catches non-JSON or malformed JSON
        app.logger.warning(f"Invalid JSON payload received: {e}")
        return jsonify(error=f"Invalid JSON: {e}"), 400

    frames = payload.get("messages") if isinstance(payload, dict) else payload
    if not isinstance(frames, list) or not frames:  # Ensure 'frames' is a non-empty list
        app.logger.warning("Expected non-empty JSON array or object with 'messages' list.")
        return jsonify(error="Expected non-empty JSON array or object with 'messages' list"), 400

    # Valid payload structure received, proceed with webhook logic and update receipt time
    if _last_successful_receipt_time:  # If this isn't the first batch of messages
        time_since_last = current_server_time - _last_successful_receipt_time
        app.logger.info(f"Time since last message batch: {time_since_last.total_seconds():.2f}s")
        if time_since_last > WEBHOOK_MESSAGE_INTERVAL:
            app.logger.info("Gap detected. Sending webhook notification: 'I hear the car whispering'")
            send_webhook_notification()
    else:
        # This is the first valid message batch since server start or reset
        app.logger.info("First valid message batch received. No prior messages to compare against for webhook.")

    _last_successful_receipt_time = current_server_time  # Update time of this valid receipt

    app.logger.info(f"Received {len(frames)} frames for processing at {current_server_time.isoformat()}.")
    points = []

    for idx, frame in enumerate(frames):
        try:
            can_id_raw = frame.get("id")
            if can_id_raw is None:
                app.logger.warning(f"Frame #{idx}: 'id' field is missing.")
                continue
            can_id = int(can_id_raw, 0)  # Allow hex (0x) or decimal

            data_raw = frame.get("data")
            if data_raw is None:
                app.logger.warning(f"Frame #{idx} (ID: {can_id:#x}): 'data' field is missing.")
                continue
            data = _bytes_from_field(data_raw)

            ts_raw_val = frame.get("timestamp")
            if ts_raw_val is None:
                app.logger.warning(f"Frame #{idx} (ID: {can_id:#x}): 'timestamp' field is missing.")
                continue
            ts_raw = float(ts_raw_val)
            ts_dt = _ts_to_datetime(ts_raw)

        except (ValueError, TypeError) as e:  # Error in converting id, data, or timestamp
            app.logger.warning(f"Frame #{idx}: Malformed basic field (id, data, or timestamp) → {e}")
            continue

        try:
            msg = db.get_message_by_frame_id(can_id)
        except KeyError:  # cantools.db.errors.UnknownMessageError is a KeyError subclass
            app.logger.warning(f"Frame #{idx}: Unknown CAN ID {can_id:#x} in DBC.")
            continue
        except Exception as e:  # Other unexpected errors from get_message_by_frame_id
            app.logger.warning(f"Frame #{idx}: Error retrieving message for CAN ID {can_id:#x} from DBC → {e}")
            continue

        try:
            # decode_choices=True: if a signal has choices, the decoded value will be a NamedSignalValue object.
            # This object has a .name (string) and .value (numeric) attribute.
            # allow_truncated=True: allows decoding if data is shorter than expected (fills with 0s).
            decoded = msg.decode(data, allow_truncated=True, decode_choices=True)
        except Exception as e:
            app.logger.warning(f"Frame #{idx} (ID: {can_id:#x}, Name: {msg.name}): Decode error → {e}")
            continue

        for signal_name, signal_value_obj in decoded.items():
            try:
                signal_def = msg.get_signal_by_name(signal_name)
                description = signal_def.comment or ""
                unit = signal_def.unit or ""
            except Exception as e:  # Should ideally be more specific if certain errors are expected
                app.logger.warning(
                    f"Frame #{idx} (Msg: {msg.name}, Signal: {signal_name}): Error getting signal definition details → {e}")
                description = ""
                unit = ""

            sensor_val = None
            signal_label = ""

            if hasattr(signal_value_obj, 'value') and hasattr(signal_value_obj, 'name'):
                # This is likely a NamedSignalValue from cantools (due to decode_choices=True)
                sensor_val = float(signal_value_obj.value)
                signal_label = str(signal_value_obj.name)
            elif isinstance(signal_value_obj, (int, float)):
                sensor_val = float(signal_value_obj)
                signal_label = str(signal_value_obj)
            else:
                # If it's some other type we can't easily convert, log and skip this signal
                app.logger.warning(
                    f"Frame #{idx} (Msg: {msg.name}, Signal: {signal_name}): Unhandled signal value type {type(signal_value_obj)}: {signal_value_obj!r}. Skipping signal.")
                continue

            pt = (
                Point("canBus")
                .tag("messageName", msg.name)
                .tag("signalName", signal_name)
                .tag("rawCAN", format(can_id, "#x"))  # Store CAN ID in hex format
                .field("sensorReading", sensor_val)
                .field("unit", unit)
                .field("description", description)
                .field("signalLabel", signal_label)  # String representation of the value (e.g., enum name)
                .time(ts_dt)
            )
            points.append(pt)

    if not points:
        app.logger.info("No points decoded from received frames – nothing to write to InfluxDB.")
        return jsonify(status="no_points_decoded", received_frames=len(frames)), 200

    try:
        # Consider reducing log verbosity for production if needed
        # for pt in points:
        #     app.logger.debug("Point to write: " + pt.to_line_protocol())
        # full_payload_preview = "\n".join(pt.to_line_protocol() for pt in points[:3]) # Preview first 3
        # app.logger.debug(f"Full InfluxDB payload preview (first 3 points if many):\n{full_payload_preview}")

        write_api.write(bucket=INFLUX_BUCKET, record=points)
        app.logger.info(f"Successfully wrote {len(points)} points to InfluxDB bucket '{INFLUX_BUCKET}'.")
    except Exception as e:
        app.logger.error(f"InfluxDB write failed: {e}")
        # Even if write fails, messages were received and processed up to this point.
        # _last_successful_receipt_time is already updated.
        return jsonify(error=f"InfluxDB write failed: {e}", written=0), 500

    return jsonify(status="ok", written=len(points), received_frames=len(frames)), 201


if __name__ == "__main__":
    app.logger.info(f"Starting CAN ingest server on port {PORT}")  # Use app.logger for consistency
    app.logger.info(f"DBC File: {DBC_FILE}")
    app.logger.info(f"InfluxDB URL: {INFLUX_URL}, Org: {INFLUX_ORG}, Bucket: {INFLUX_BUCKET}")
    app.logger.info(
        f"Webhook notifications enabled. URL: {WEBHOOK_URL}, Interval: {WEBHOOK_MESSAGE_INTERVAL.total_seconds()}s")
    app.run(host="0.0.0.0", port=PORT)