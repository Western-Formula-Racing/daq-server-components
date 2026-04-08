from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    stream_with_context,
    Response,
)
import uuid, time, threading, json, io, logging, requests, os, asyncio
from helper import CANInfluxStreamer
import traceback

if os.getenv("DEBUG") is None:
    from dotenv import load_dotenv
    load_dotenv()

error_logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {"csv"}
ALLOWED_DBC_EXTENSIONS = {"dbc"}
PROGRESS = {}
CURRENT_FILE = {"name": "", "task_id": "", "bucket": ""}
WEBHOOK_URL = (
    os.getenv("FILE_UPLOADER_WEBHOOK_URL")
    or os.getenv("SLACK_WEBHOOK_URL")
    or ""
)
DEBUG: bool = bool(int(os.getenv("DEBUG") or 0))
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://influxdb3:8181")
INFLUXDB_DATABASE = os.getenv("INFLUXDB_DATABASE", "WFR")
app = Flask(__name__)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _seasons_from_env() -> list[str]:
    """Fallback: parse SEASONS env var (format: 'WFR25:2025,WFR26:2026')."""
    raw = os.getenv("SEASONS", "")
    seasons = [part.split(":")[0].strip() for part in raw.split(",") if part.strip()]
    return sorted(seasons, reverse=True) if seasons else ["WFR26", "WFR25"]


def getSeasons() -> list[str]:
    """Return list of season/table names from the WFR database, falling back to env var."""
    api_url = f"{INFLUXDB_URL.rstrip('/')}/api/v3/query_sql"
    try:
        res = requests.post(
            api_url,
            headers={"Authorization": f"Token {INFLUXDB_TOKEN}", "Content-Type": "application/json"},
            json={"db": INFLUXDB_DATABASE, "q": "SELECT DISTINCT table_name FROM information_schema.tables", "format": "json"},
            timeout=10,
        )
        res.raise_for_status()
        seasons = [row["table_name"] for row in res.json() if not row["table_name"].startswith("_")]
        return sorted(seasons, reverse=True) if seasons else _seasons_from_env()
    except Exception:
        return _seasons_from_env()


# This function can send Slack messages to a channel
def send_webhook_notification(payload_text=None):
    if not WEBHOOK_URL:
        return
    try:
        payload = {"text": payload_text}
        response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
        error_logger.info("Webhook notification sent successfully.")
    except requests.exceptions.RequestException as e:
        error_logger.error(f"Webhook notification failed: {e}")


@app.route("/")
def index():
    return render_template(
        "index.html",
        file_name=CURRENT_FILE["name"],
        task_id=CURRENT_FILE["task_id"],
        current_bucket=CURRENT_FILE["bucket"],
        bucket_names=getSeasons(),
    )


@app.route("/create-bucket", methods=["POST"])
def create_bucket():
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"error": "No bucket name provided"}), 400
    api_url = f"{INFLUXDB_URL.rstrip('/')}/api/v3/configure/database"
    res = requests.post(
        api_url,
        headers={"Authorization": f"Token {INFLUXDB_TOKEN}", "Content-Type": "application/json"},
        json={"db": name},
        timeout=10,
    )
    if res.status_code in (200, 201, 204):
        return jsonify({"name": name})
    return jsonify({"error": res.text}), res.status_code


@app.route("/upload", methods=["POST"])
def upload_file():
    if request.method == "POST":
        if CURRENT_FILE["task_id"]:
            return (
                jsonify(
                    {
                        "error": "A File Is Already Being Uploaded, Please Wait For The Upload To Finish"
                    }
                ),
                400,
            )
        bucket = request.form.get("bucket")
        if not bucket or bucket == "":
            return "No Bucket Provided", 400
        
        # Handle optional custom DBC file
        dbc_temp_path = None
        dbc_file = request.files.get("dbc")
        if dbc_file and dbc_file.filename:
            if not dbc_file.filename.lower().endswith(".dbc"):
                return "Invalid DBC file type. Only .dbc files allowed.", 400
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".dbc") as tmp:
                dbc_file.save(tmp)
                dbc_temp_path = tmp.name
            error_logger.info(f"Custom DBC uploaded: {dbc_file.filename} -> {dbc_temp_path}")

        # Handle multiple CSV files
        files = request.files.getlist("file")
        if not files or len(files) == 0:
            return "No Files Provided", 400

        # Validate all files are CSV
        for f in files:
            if not f or not f.filename or f.filename == "":
                return "Empty file provided", 400
            content_type = f.mimetype or ""
            filename = f.filename or ""
            if content_type != "text/csv" and not filename.lower().endswith('.csv'):
                return f"Invalid File Type: {filename}. Only CSV files allowed.", 400

        # Calculate total size of all files
        total_size = 0
        file_data = []
        for f in files:
            data = f.read()
            total_size += len(data)
            file_data.append((f.filename or "unknown.csv", data))
            f.seek(0)  # Reset for potential re-read

        task_id = str(uuid.uuid4())
        PROGRESS[task_id] = {"pct": 0, "msg": "Starting...", "done": False}
        file_names = [f.filename or "unknown.csv" for f in files]
        CURRENT_FILE["name"] = f"{len(files)} CSV files: {', '.join(file_names[:3])}{'...' if len(files) > 3 else ''}"
        CURRENT_FILE["task_id"] = str(task_id)
        CURRENT_FILE["bucket"] = bucket
        send_webhook_notification(f"Uploading {len(files)} CSV files -> {bucket}: {', '.join(file_names[:3])}{'...' if len(files) > 3 else ''}")

        def on_progress(sent: int, total: int):
            try:
                pct = int((sent * 100) / total) if total else 0
                PROGRESS[task_id]["pct"] = pct
                PROGRESS[task_id]["sent"] = sent
                PROGRESS[task_id]["total"] = total
                PROGRESS[task_id]["name"] = CURRENT_FILE["name"]
                PROGRESS[task_id]["bucket"] = bucket
                PROGRESS[task_id]["msg"] = f"Processing... {pct}% ({sent}/{total} rows)"
                if sent >= total and not PROGRESS[task_id].get("done"):
                    PROGRESS[task_id]["done"] = True
                    send_webhook_notification(
                        f"File Done Uploading: {CURRENT_FILE['name']} -> {CURRENT_FILE['bucket']}"
                    )
            except:
                pass

        def worker():
            # Auto-configure streamer for file size with InfluxDB-safe settings
            file_size_mb = total_size / (1024 * 1024)
            streamer = CANInfluxStreamer(bucket=INFLUXDB_DATABASE, table=bucket, dbc_path=dbc_temp_path)

            try:
                # Process multiple CSV files using the new method
                asyncio.run(
                    streamer.stream_multiple_csvs(
                        file_data=file_data,
                        on_progress=on_progress,
                        total_size_mb=file_size_mb
                    )
                )

            except Exception as e:
                error_logger.error(e)
                error_logger.error(traceback.format_exc())
                PROGRESS[task_id]["msg"] = f"Error: {e}"
                PROGRESS[task_id]["done"] = True
            finally:
                try:
                    streamer.close()
                except Exception as e:
                    print("error closing streamer", e)
                    pass
                if dbc_temp_path and os.path.exists(dbc_temp_path):
                    try:
                        os.unlink(dbc_temp_path)
                    except Exception:
                        pass

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"task_id": task_id})
    return "bad request", 400


@app.route("/progress/<task_id>")
def progress_stream(task_id):
    @stream_with_context
    def gen():
        yield "retry: 1000\n\n"
        last_pct = -1
        while True:
            state: dict = PROGRESS.get(task_id) or {}
            if state is None:
                payload = json.dumps(
                    {
                        "error": "Unknown task / unable to find task_id",
                    }
                )
                yield f"event: error \ndata: {payload}\n\n"
                break
            if "pct" in state and state["pct"] != last_pct:
                last_pct = state["pct"]
                payload = json.dumps(state)
                yield f"data: {payload}\n\n"
            if state.get("done"):
                CURRENT_FILE["name"] = ""
                CURRENT_FILE["task_id"] = ""
                CURRENT_FILE["bucket"] = ""
                yield f"data: {json.dumps(state)}\n\n"
                break
            time.sleep(0.3)

    return Response(
        response=gen(),  # type: ignore
        status=200,
        headers={"Cache-Control": "no-cache"},
        content_type="text/event-stream",
    )


@app.route("/health")
def health_check():
    return jsonify(
        {"status": "healthy", "timestamp": time.time(), "progress_array": PROGRESS}
    )


if __name__ == "__main__":
    if DEBUG:
        app.run(host="0.0.0.0", port=5001, debug=True)  # Comment out for prod
    else:
        app.run(host="0.0.0.0", port=8084, debug=False, use_reloader=False)  # Comment out for prod
