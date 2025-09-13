#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask, request, jsonify, render_template, Response
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import WriteOptions
from datetime import datetime, timezone, timedelta
import cantools
import os
import logging
import requests
from collections import deque
import threading
import queue  # For log streaming
import time  # For log streaming
import json  # For manual JSON parsing if needed

# ─── CONFIG ────────────────────────────────────────────────────────────────
INFLUX_URL = os.getenv("INFLUXDB_URL", "http://influxdb2:8086")
INFLUX_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUX_ORG = os.getenv("INFLUXDB_ORG", "WFR")
INFLUX_BUCKET = os.getenv("INFLUXDB_BUCKET")
DBC_FILE = os.getenv("DBC_FILE")
PORT = int(os.getenv("PORT", "8085"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://hooks.slack.com/services/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")
# This is the no data for a while message
WEBHOOK_MESSAGE_INTERVAL = timedelta(minutes=1)

# ─── RELATIVE‐TIMESTAMP ANCHORING ─────────────────────────────────────────
_reset_threshold = timedelta(seconds=60)
_first_relative = True
_relative_anchor_ts = 0.0
_relative_anchor_real = datetime.now(timezone.utc)
_last_raw_ts = 0.0

# ─── WEBHOOK STATE ─────────────────────────────────────────────────────────
_last_successful_receipt_time = None
_last_whisper = None
_fallen_once = False

# ─── PACKET STATISTICS FOR GUI ─────────────────────────────────────────────
packet_history = deque()
history_lock = threading.Lock()
MAX_HISTORY_SECONDS = 30

# ─── CACHING FOR PERFORMANCE ───────────────────────────────────────────────
signal_definition_cache = {}
signal_cache_lock = threading.Lock()
message_cache = {}  # Cache DBC message lookups by CAN ID
can_id_hex_cache = {}  # Cache hex string conversions

# ─── LOG STREAMING SETUP ───────────────────────────────────────────────────
log_queue = queue.Queue()  # Thread-safe queue to hold log messages


class QueueLogHandler(logging.Handler):
    def __init__(self, log_queue_instance):
        super().__init__()
        self.log_queue = log_queue_instance
        self.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))

    def emit(self, record):
        log_entry = self.format(record)
        self.log_queue.put(log_entry)


# ─── FLASK APP & LOGGER SETUP ──────────────────────────────────────────────
app = Flask(__name__)

file_handler = logging.FileHandler("listener.log")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s: %(message)s [%(module)s:%(lineno)d in %(funcName)s]", datefmt="%Y-%m-%d %H:%M:%S"))
app.logger.addHandler(file_handler)

queue_log_handler = QueueLogHandler(log_queue)
queue_log_handler.setLevel(logging.ERROR)  # Only show errors in the Server Logs window
app.logger.addHandler(queue_log_handler)
app.logger.setLevel(logging.INFO)

werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.addHandler(queue_log_handler)
werkzeug_logger.setLevel(logging.ERROR)  # Suppress HTTP access logs, only show errors

# ─── LOAD DBC & INFLUX CLIENT ─────────────────────────────────────────────
try:
    db = cantools.database.load_file(DBC_FILE)
    app.logger.debug(f"Successfully loaded DBC: {DBC_FILE}")
except Exception as e:
    app.logger.critical(f"Failed to load DBC file: {DBC_FILE} - {e}")
    if 'queue_log_handler' in globals():
        log_queue.put(
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} [CRITICAL] root: Failed to load DBC file: {DBC_FILE} - {e}")
    raise SystemExit(f"Failed to load DBC file: {e}")

# Validate InfluxDB token
if not INFLUX_TOKEN:
    error_msg = "❌ No InfluxDB token found in environment. Make sure INFLUXDB_TOKEN is set."
    app.logger.critical(error_msg)
    if 'queue_log_handler' in globals():
        log_queue.put(f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} [CRITICAL] root: {error_msg}")
    raise SystemExit(error_msg)

client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
# Optimized write options for better throughput
write_api = client.write_api(write_options=WriteOptions(
    batch_size=1000,  # Increased batch size
    flush_interval=500,  # Reduced flush interval for lower latency
    jitter_interval=100,  # Add jitter to prevent thundering herd
    retry_interval=5000,  # Retry failed writes
    max_retries=3,  # Maximum retry attempts
    max_retry_delay=30000,  # Maximum retry delay
    exponential_base=2  # Exponential backoff base
))


def _bytes_from_field(data_field):
    """Optimized data field parsing with type checking."""
    if isinstance(data_field, list):
        return bytes(int(b) & 0xFF for b in data_field)
    if isinstance(data_field, str):
        return bytes(int(b, 16 if b.lower().startswith("0x") else 10) & 0xFF
                     for b in data_field.split())
    if data_field is None:
        return b''
    raise ValueError(f"Unrecognized data format for _bytes_from_field: {type(data_field)} {data_field!r}")


def _get_cached_message(can_id):
    """Get DBC message with caching to avoid repeated lookups."""
    if can_id in message_cache:
        return message_cache[can_id]
    
    try:
        msg = db.get_message_by_frame_id(can_id)
        message_cache[can_id] = msg
        return msg
    except KeyError:
        message_cache[can_id] = None  # Cache the fact that this ID is unknown
        return None


def _get_cached_hex_id(can_id):
    """Get cached hex string representation of CAN ID."""
    if can_id not in can_id_hex_cache:
        can_id_hex_cache[can_id] = format(can_id, "#04x")
    return can_id_hex_cache[can_id]


def _cleanup_caches_if_needed():
    """Periodically clean up caches to prevent memory leaks."""
    # Clean up message cache if it gets too large
    if len(message_cache) > 1000:  # Reasonable limit for CAN IDs
        app.logger.info(f"Message cache size ({len(message_cache)}) exceeded limit, clearing cache")
        message_cache.clear()
    
    # Clean up hex ID cache if it gets too large
    if len(can_id_hex_cache) > 1000:
        app.logger.info(f"CAN ID hex cache size ({len(can_id_hex_cache)}) exceeded limit, clearing cache")
        can_id_hex_cache.clear()
    
    # Signal cache is less likely to grow unbounded, but monitor it
    if len(signal_definition_cache) > 5000:  # Signals per message * reasonable message count
        app.logger.info(f"Signal cache size ({len(signal_definition_cache)}) exceeded limit, clearing cache")
        with signal_cache_lock:
            signal_definition_cache.clear()


def _ts_to_datetime(ts: float) -> datetime:
    global _first_relative, _relative_anchor_ts, _relative_anchor_real, _last_raw_ts
    
    # Check if timestamp is in milliseconds (likely if > year 2001 in milliseconds)
    # Unix timestamp for 2001-01-01 in milliseconds is ~978,307,200,000
    if ts > 978_307_200_000:
        # Convert milliseconds to seconds
        ts_seconds = ts / 1000.0
        app.logger.debug(f"Converting millisecond timestamp {ts} to seconds {ts_seconds}")
        return datetime.fromtimestamp(ts_seconds, timezone.utc)
    elif ts > 946_684_800:
        # Already in seconds, use as-is
        app.logger.debug(f"Using timestamp {ts} as seconds (absolute)")
        return datetime.fromtimestamp(ts, timezone.utc)

    # Handle relative timestamps (small values that need anchoring)
    current_time = datetime.now(timezone.utc)
    
    # Check if this might be a relative timestamp in milliseconds
    # If it's larger than typical relative seconds but smaller than absolute milliseconds
    original_ts = ts
    if ts > 1_000_000:  # Likely milliseconds for relative timestamps
        ts = ts / 1000.0  # Convert to seconds for processing
        app.logger.debug(f"Converting relative millisecond timestamp {original_ts} to seconds {ts}")
    
    if _first_relative:
        _relative_anchor_real = current_time
        _relative_anchor_ts = ts
        _last_raw_ts = ts
        _first_relative = False
        app.logger.debug(
            f"Anchoring relative timestamp: real={_relative_anchor_real.isoformat()}, device_ts={_relative_anchor_ts}")
        return _relative_anchor_real

    if ts < _last_raw_ts and (_last_raw_ts - ts) > _reset_threshold.total_seconds():
        app.logger.debug(f"Relative timestamp reset detected: old_ts={_last_raw_ts}, new_ts={ts}. Re-anchoring.")
        _relative_anchor_real = current_time
        _relative_anchor_ts = ts

    _last_raw_ts = ts
    elapsed_seconds = ts - _relative_anchor_ts
    if elapsed_seconds < 0:
        app.logger.warning(
            f"Negative elapsed time ({elapsed_seconds}s) for relative ts {ts} vs anchor {_relative_anchor_ts}. Using current.")
        if abs(elapsed_seconds) > _reset_threshold.total_seconds() / 2:
            _relative_anchor_real = current_time
            _relative_anchor_ts = ts
            app.logger.debug(
                f"Re-anchoring due to significant negative jump: real={_relative_anchor_real.isoformat()}, device_ts={_relative_anchor_ts}")
            return _relative_anchor_real
        return _relative_anchor_real

    elapsed = timedelta(seconds=elapsed_seconds)
    return _relative_anchor_real + elapsed


def send_webhook_notification(payload_text=None):
    try:
        payload = {"text": payload_text}
        response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
        app.logger.info("Webhook notification sent successfully.")
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Webhook notification failed: {e}")


@app.route("/can", methods=["POST"])
def ingest_can():
    global _last_successful_receipt_time, packet_history, history_lock, signal_definition_cache, signal_cache_lock, _fallen_once
    current_server_time = datetime.now(timezone.utc)
    http_payload_size_for_batch = 0

    try:
        actual_request_body_bytes = request.get_data(cache=True, as_text=False)
        http_payload_size_for_batch = len(actual_request_body_bytes)
    except Exception as e:
        app.logger.error(f"Could not read request data for HTTP payload size: {e}")
        # http_payload_size_for_batch remains 0

    try:
        payload = request.get_json(force=True)
        if payload is None and http_payload_size_for_batch > 0:
            app.logger.warning(
                f"get_json returned None for a request with HTTP payload size {http_payload_size_for_batch}B from {request.remote_addr}. Raw data: {actual_request_body_bytes[:200]!r}")
    except Exception as e:
        app.logger.warning(
            f"Invalid JSON payload from {request.remote_addr} (HTTP size: {http_payload_size_for_batch}B): {e}")
        return jsonify(error=f"Invalid JSON: {str(e)}"), 400

    frames = payload.get("messages") if isinstance(payload, dict) else payload
    if not isinstance(frames, list):
        app.logger.warning(
            f"Expected JSON array or 'messages' list from {request.remote_addr}. Payload type: {type(payload)}, HTTP size: {http_payload_size_for_batch}B.")
        return jsonify(error="Expected JSON array or object with 'messages' list"), 400


    # Update last receipt time if we got any valid POST, even with empty frames list
    # This helps webhook know the source is alive but just not sending data frames.
    if http_payload_size_for_batch > 0 or frames:  # If payload was received OR frames were parsed (even if empty list now)
        _last_successful_receipt_time = current_server_time

    # Periodically clean up caches (every ~100 requests with frames)
    if frames and len(frames) > 0 and (len(message_cache) + len(can_id_hex_cache)) % 100 == 0:
        _cleanup_caches_if_needed()

    num_received_frames_in_batch = len(frames) if frames else 0
    total_can_data_size_in_batch = 0
    if frames:  # Only process if frames is not None and potentially has items
        for frame_content_for_stats in frames:
            data_raw_for_stats = frame_content_for_stats.get("data")
            try:
                total_can_data_size_in_batch += len(_bytes_from_field(data_raw_for_stats))
            except ValueError as e:
                app.logger.warning(f"Malformed 'data' field for CAN data stats: {data_raw_for_stats}, error: {e}")
    else:  # No frames to process (e.g. empty list received)
        app.logger.debug(
            f"Received 0 frames to process from {request.remote_addr} (HTTP size: {http_payload_size_for_batch}B).")

    # Update packet history in a single batch operation
    if num_received_frames_in_batch > 0 or http_payload_size_for_batch > 0:
        current_server_time = datetime.now(timezone.utc)
        with history_lock:
            packet_history.append((current_server_time, num_received_frames_in_batch, total_can_data_size_in_batch,
                                   http_payload_size_for_batch))
            # Batch cleanup - remove old entries
            cutoff_for_deque = current_server_time - timedelta(seconds=MAX_HISTORY_SECONDS)
            while packet_history and packet_history[0][0] < cutoff_for_deque:
                packet_history.popleft()

    # Log summary only, not individual frame details unless debug logging is enabled
    if num_received_frames_in_batch > 0 and app.logger.isEnabledFor(logging.DEBUG):
        app.logger.debug(
            f"Processing {num_received_frames_in_batch} frames (CAN: {total_can_data_size_in_batch}B, HTTP: {http_payload_size_for_batch}B) from {request.remote_addr}")
    elif num_received_frames_in_batch == 0 and app.logger.isEnabledFor(logging.DEBUG):
        app.logger.debug(
            f"Received 0 frames from {request.remote_addr} (HTTP: {http_payload_size_for_batch}B)")

    points = []
    frame_errors = 0  # Track processing errors for summary logging
    if frames:  # Iterate only if frames exist
        for idx, frame in enumerate(frames):
            try:
                can_id_raw = frame.get("id")
                if can_id_raw is None:
                    if app.logger.isEnabledFor(logging.WARNING):
                        app.logger.warning(f"Frame #{idx + 1}: 'id' missing. Skipping. Content: {frame}")
                    frame_errors += 1
                    continue
                can_id = int(can_id_raw, 0) if isinstance(can_id_raw, str) else int(can_id_raw)

                data_raw = frame.get("data")
                data = _bytes_from_field(data_raw)

                ts_raw_val = frame.get("timestamp")
                if ts_raw_val is None:
                    if app.logger.isEnabledFor(logging.WARNING):
                        app.logger.warning(f"Frame #{idx + 1} (ID: {can_id:#x}): 'timestamp' missing. Skipping.")
                    frame_errors += 1
                    continue
                ts_raw = float(ts_raw_val)
                ts_dt = _ts_to_datetime(ts_raw)

            except (ValueError, TypeError) as e:
                if app.logger.isEnabledFor(logging.WARNING):
                    app.logger.warning(f"Frame #{idx + 1}: Malformed basic field. Error: {e}. Skipping.")
                frame_errors += 1
                continue
            except Exception as e:
                app.logger.error(f"Frame #{idx + 1}: Unexpected error in basic fields. Error: {e}. Skipping.")
                frame_errors += 1
                continue

            # Use cached message lookup
            msg = _get_cached_message(can_id)
            if msg is None:
                # Only log unknown CAN IDs at debug level to reduce noise
                if app.logger.isEnabledFor(logging.DEBUG):
                    app.logger.debug(f"Frame #{idx + 1}: Unknown CAN ID {can_id:#x} in DBC. Skipping.")
                continue

            try:
                decoded_signals = msg.decode(data, allow_truncated=True, decode_choices=True)
            except Exception as e:
                if app.logger.isEnabledFor(logging.WARNING):
                    app.logger.warning(f"Frame #{idx + 1} (ID: {can_id:#x}, Name: {msg.name}): Decode error. Error: {e}. Skipping.")
                frame_errors += 1
                continue

            # Pre-compute values used multiple times
            can_id_hex = _get_cached_hex_id(can_id)
            
            for signal_name_from_decode, signal_value_obj in decoded_signals.items():
                signal_def = None
                cache_key = (msg.name, signal_name_from_decode)
                
                # Minimize lock time by checking cache first
                with signal_cache_lock:
                    signal_def = signal_definition_cache.get(cache_key)
                
                if signal_def is None:
                    try:
                        current_signal_def = msg.get_signal_by_name(signal_name_from_decode)
                        with signal_cache_lock:
                            signal_definition_cache[cache_key] = current_signal_def
                        signal_def = current_signal_def
                    except Exception as e:
                        if app.logger.isEnabledFor(logging.WARNING):
                            app.logger.warning(f"Frame #{idx + 1} (Msg: {msg.name}, Sig: {signal_name_from_decode}): No signal definition. Skipping signal.")
                        continue

                unit = signal_def.unit if signal_def.unit is not None else "N/A"
                actual_signal_name = signal_def.name
                sensor_val, signal_label = None, ""

                if hasattr(signal_value_obj, 'value') and hasattr(signal_value_obj, 'name'):
                    try:
                        sensor_val = float(signal_value_obj.value)
                        signal_label = str(signal_value_obj.name)
                    except (ValueError, TypeError) as e:
                        if app.logger.isEnabledFor(logging.WARNING):
                            app.logger.warning(f"Frame #{idx + 1} (Msg: {msg.name}, Sig: {actual_signal_name}): Error converting enum. Skipping.")
                        continue
                elif isinstance(signal_value_obj, (int, float)):
                    sensor_val = float(signal_value_obj)
                    signal_label = str(signal_value_obj)
                else:
                    if app.logger.isEnabledFor(logging.WARNING):
                        app.logger.warning(f"Frame #{idx + 1} (Msg: {msg.name}, Sig: {actual_signal_name}): Unhandled type {type(signal_value_obj)}. Skipping.")
                    continue

                pt = (Point("canBus").tag("messageName", msg.name).tag("signalName", actual_signal_name)
                      .tag("canID", can_id_hex).field("sensorReading", sensor_val)
                      .field("unit", unit).field("signalLabel", signal_label).time(ts_dt))
                points.append(pt)

    # Log processing summary
    if frame_errors > 0:
        app.logger.warning(f"Processed {num_received_frames_in_batch} frames with {frame_errors} errors, generated {len(points)} points")
    elif num_received_frames_in_batch > 0 and app.logger.isEnabledFor(logging.DEBUG):
        app.logger.debug(f"Successfully processed {num_received_frames_in_batch} frames, generated {len(points)} points")

    if not points:
        if app.logger.isEnabledFor(logging.DEBUG):
            app.logger.debug(f"No points decoded from {num_received_frames_in_batch} frames – nothing to write to InfluxDB.")
        return jsonify(status="no_points_decoded", received_frames=num_received_frames_in_batch, written_points=0), 200

    global _last_whisper
    try:
        write_api.write(bucket=INFLUX_BUCKET, record=points)
        if app.logger.isEnabledFor(logging.DEBUG):
            app.logger.debug(f"Successfully wrote {len(points)} points to InfluxDB from {num_received_frames_in_batch} frames.")
        
        # Optimized webhook notification timing
        if _last_whisper is None or (current_server_time - _last_whisper) > WEBHOOK_MESSAGE_INTERVAL and not _fallen_once:
            send_webhook_notification(payload_text="I hear the car whispering.")
            _last_whisper = current_server_time
            _fallen_once = True  # Reset fallen state on successful write

    except Exception as e:
        app.logger.error(f"InfluxDB write failed for {len(points)} points. Error: {e}")
        return jsonify(error=f"InfluxDB write failed: {str(e)}", written_points=0,
                       received_frames=num_received_frames_in_batch), 500

    return jsonify(status="ok", written_points=len(points), received_frames=num_received_frames_in_batch), 201


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/status")
def status():
    global _last_successful_receipt_time, packet_history, history_lock, signal_definition_cache, message_cache, can_id_hex_cache
    now = datetime.now(timezone.utc)
    cutoff_time_60s = now - timedelta(seconds=60)

    packets_in_last_60s = 0
    total_can_data_in_last_60s_bytes = 0
    total_http_payload_in_last_60s_bytes = 0
    last_batch_can_data_size_bytes = 0
    last_batch_http_payload_size_bytes = 0

    # Minimize lock time by taking a snapshot
    with history_lock:
        current_history_snapshot = list(packet_history)

    if current_history_snapshot:
        last_batch_can_data_size_bytes = current_history_snapshot[-1][2]
        last_batch_http_payload_size_bytes = current_history_snapshot[-1][3]

    for ts_hist, num_frames_hist, can_size_hist, http_size_hist in current_history_snapshot:
        if ts_hist > cutoff_time_60s:
            packets_in_last_60s += num_frames_hist
            total_can_data_in_last_60s_bytes += can_size_hist
            total_http_payload_in_last_60s_bytes += http_size_hist

    receiver_status_message = "Initializing..."
    if _last_successful_receipt_time:
        time_since_last_receipt_seconds = (now - _last_successful_receipt_time).total_seconds()
        if time_since_last_receipt_seconds <= 10:
            receiver_status_message = f"Active (last data {time_since_last_receipt_seconds:.1f}s ago)"
        elif time_since_last_receipt_seconds <= MAX_HISTORY_SECONDS + 10:  # Allow some buffer
            receiver_status_message = f"Monitoring (last data {time_since_last_receipt_seconds:.0f}s ago)"
        else:
            receiver_status_message = f"Inactive (last data {time_since_last_receipt_seconds:.0f}s ago)"
    else:
        receiver_status_message = "Awaiting Data (no messages received yet)"

    return jsonify({
        "receiver_status": receiver_status_message,
        "packets_last_60s": packets_in_last_60s,
        "total_can_data_last_60s_bytes": total_can_data_in_last_60s_bytes,
        "can_data_rate_last_60s_bytes_sec": total_can_data_in_last_60s_bytes / 60.0 if packets_in_last_60s > 0 else 0,
        "total_http_payload_last_60s_bytes": total_http_payload_in_last_60s_bytes,
        "http_payload_rate_last_60s_bytes_sec": total_http_payload_in_last_60s_bytes / 60.0 if packets_in_last_60s > 0 else 0,
        "last_batch_can_data_size_bytes": last_batch_can_data_size_bytes,
        "last_batch_http_payload_size_bytes": last_batch_http_payload_size_bytes,
        "last_successful_receipt_time_iso": _last_successful_receipt_time.isoformat() if _last_successful_receipt_time else None,
        "current_server_time_iso": now.isoformat(),
        "signal_cache_size": len(signal_definition_cache),
        "message_cache_size": len(message_cache),
        "can_id_hex_cache_size": len(can_id_hex_cache),
        "packet_history_size": len(packet_history)
    })


@app.route('/log-stream')
def log_stream():
    def generate_logs():
        initial_message = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} [INFO] LogStream: Client connected to log stream.\n"
        yield f"data: {initial_message}\n\n"
        while True:
            try:
                log_entry = log_queue.get(timeout=5)
                yield f"data: {log_entry}\n\n"
            except queue.Empty:
                yield ": heartbeat\n\n"

    headers = {'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', 'Connection': 'keep-alive',
               'X-Accel-Buffering': 'no'}
    return Response(generate_logs(), headers=headers)


def watchdog_thread():
    global _last_successful_receipt_time, _fallen_once
    while True:
        now = datetime.now(timezone.utc)
        if _last_successful_receipt_time and (now - _last_successful_receipt_time) > WEBHOOK_MESSAGE_INTERVAL and _fallen_once:
            send_webhook_notification(payload_text="The whisper has faded into silence... Has the vessel fallen still?")
            _fallen_once = False
        time.sleep(10)  # check every 10 seconds

threading.Thread(target=watchdog_thread, daemon=True).start()

if __name__ == "__main__":
    app.logger.info(f"Starting CAN ingest server on port {PORT}")
    app.logger.debug(f"DBC File: {DBC_FILE}")
    app.logger.debug(f"InfluxDB URL: {INFLUX_URL}, Org: {INFLUX_ORG}, Bucket: {INFLUX_BUCKET}")
    app.logger.debug(f"InfluxDB Token: {'✅ Configured' if INFLUX_TOKEN else '❌ Missing'}")
    app.logger.info(f"Webhook notifications to Slack enabled. Interval: {WEBHOOK_MESSAGE_INTERVAL.total_seconds()}s")
    app.logger.debug(f"Log file: listener.log")
    app.logger.debug(f"Log streaming available at /log-stream")
    app.logger.debug(f"GUI (if index.html is present) available at http://0.0.0.0:{PORT}/")

    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)