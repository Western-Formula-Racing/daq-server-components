# on the server
from flask import Flask, request, jsonify
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import WriteOptions
from datetime import datetime, timezone, timedelta
import cantools, os, logging

# ─── CONFIG ────────────────────────────────────────────────────────────────
INFLUX_URL    = os.getenv("INFLUX_URL", "http://influxwfr:8086")
INFLUX_TOKEN  = os.getenv("INFLUX_TOKEN", "s9XkBC7pKOlb92-N9M40qilmxxoBe4wrnki4zpS_o0QSVTuMSQRQBerQB9Zv0YV40tmYayuX3w4G2MNizdy3qw==")
INFLUX_ORG    = os.getenv("INFLUX_ORG", "WFR")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "ourCar")
DBC_FILE      = os.getenv("DBC_FILE", "testing_data/20240129 Gen5 CAN DB.dbc")
PORT          = int(os.getenv("PORT", "8085"))
# ────────────────────────────────────────────────────────────────────────────

# Load DBC at startup
try:
    db = cantools.database.load_file(DBC_FILE)
    print(f"Loaded DBC: {DBC_FILE}")
except Exception as e:
    raise SystemExit(f"Failed to load DBC file: {e}")

# Prepare Influx client + write_api
client    = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
write_api = client.write_api(write_options=WriteOptions(batch_size=500, flush_interval=1000))

app = Flask(__name__)

# —— Optional: log to file as well as stderr ——————————————
file_handler = logging.FileHandler("listener.log")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.ERROR)


def _bytes_from_field(data_field):
    """Convert incoming 'data' (list[str|int] or str) into a bytes object."""
    if isinstance(data_field, list):
        return bytes(int(b) & 0xFF for b in data_field)
    if isinstance(data_field, str):
        return bytes(int(b, 16 if b.lower().startswith("0x") else 10) & 0xFF for b in data_field.split())
    raise ValueError(f"Unrecognized data format: {data_field!r}")


def _ts_to_datetime(ts: float) -> datetime:
    """Convert the incoming numeric timestamp to an aware UTC datetime.

    If the sender gives absolute Unix‑epoch seconds, use them directly.
    If the value looks like a small relative timestamp (e.g. < year 2000),
    map it to *now* minus that relative offset so points show up in recent dashboards.
    """
    if ts > 946_684_800:      # 2000‑01‑01 in epoch seconds
        return datetime.fromtimestamp(ts, timezone.utc)
    # Treat as relative seconds since log start — anchor to now.
    return datetime.now(timezone.utc) - timedelta(seconds=(max(0.0, ts)))


@app.route("/can", methods=["POST"])
def ingest_can():
    """Ingest JSON CAN frames, decode with DBC, and write to InfluxDB."""
    try:
        payload = request.get_json(force=True)
    except Exception as e:
        return jsonify(error=f"Invalid JSON: {e}"), 400

    # Accept top‑level list or object with "messages"
    frames = payload.get("messages") if isinstance(payload, dict) else payload
    if not isinstance(frames, list):
        return jsonify(error="Expected JSON array or object with 'messages' list"), 400

    app.logger.info(f"Received {len(frames)} frames")
    points = []

    for idx, frame in enumerate(frames):
        try:
            can_id = int(frame["id"], 0)  # handles "0x1A" or "26"
            data   = _bytes_from_field(frame["data"])
            ts_raw = float(frame["timestamp"])
            ts_dt  = _ts_to_datetime(ts_raw)
            msg    = db.get_message_by_frame_id(can_id)
        except (KeyError, ValueError) as e:
            app.logger.warning(f"Frame #{idx}: malformed or missing field → {e}")
            continue
        except Exception as e:
            app.logger.warning(f"Frame #{idx}: DBC error → {e}")
            continue

        try:
            decoded = msg.decode(data)
        except Exception as e:
            app.logger.warning(f"Frame #{idx}: decode error → {e}")
            continue

        for signal_name, value in decoded.items():
            try:
                signal = msg.get_signal_by_name(signal_name)
                description = signal.comment or ""
                unit        = signal.unit or ""
            except Exception:
                description = ""
                unit = ""

            if hasattr(value, "value"):
                sensor_val   = float(value.value)
                signal_label = value.name
            else:
                sensor_val   = float(value)
                signal_label = str(value)

            pt = (
                Point("canBus")
                .tag("messageName", msg.name)
                .tag("signalName", signal_name)
                .tag("rawCAN", format(can_id, "#x"))
                .field("sensorReading", sensor_val)
                .field("unit", unit)
                .field("description", description)
                .field("signalLabel", signal_label)
                .time(ts_dt)
            )
            points.append(pt)

    if not points:
        app.logger.info("No points decoded – nothing to write.")
        return jsonify(status="no_points"), 200

    try:
        # log raw line-protocol for each Point
        for pt in points:
            app.logger.info("LP: " + pt.to_line_protocol())
            # also log the complete batch payload as a single string
        full_payload = "\n".join(pt.to_line_protocol() for pt in points)
        app.logger.info("Full InfluxDB payload:\n%s", full_payload)
        # write to influx
        write_api.write(bucket=INFLUX_BUCKET, record=points)
        app.logger.info(f"Wrote {len(points)} points to InfluxDB bucket '{INFLUX_BUCKET}'")
    except Exception as e:
        app.logger.error(f"Influx write failed: {e}")
        return jsonify(error=f"Influx write failed: {e}"), 500

    return jsonify(status="ok", written=len(points)), 201


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
