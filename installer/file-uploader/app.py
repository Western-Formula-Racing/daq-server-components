from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    stream_with_context,
    Response,
)
import uuid, time, threading, json, io, logging, requests, os
from helper import CANInfluxStreamer
import traceback

if os.getenv("DEBUG") is None:
    from dotenv import load_dotenv
    load_dotenv()

error_logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {"csv", "zip"}
PROGRESS = {}
CURRENT_FILE = {"name": "", "task_id": "", "bucket": ""}
WEBHOOK_URL = os.getenv("WEBHOOK_URL") or ""
DEBUG: bool = bool(int(os.getenv("DEBUG") or 0))
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
app = Flask(__name__)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def getBuckets() -> list[str]:
    #TODO: DEBUG mode will be changed to AWS/Local mode
    if DEBUG:
        res = requests.get(
            "http://3.98.181.12:8086/api/v2/buckets",
            headers={"Authorization": f"Token {INFLUXDB_TOKEN}"},
        ).json()
    else:
        res = requests.get(
            "http://influxdb2:8086/api/v2/buckets",
            headers={"Authorization": f"Token {INFLUXDB_TOKEN}"},
        ).json()
    names: list[str] = [bucket["name"] for bucket in res["buckets"]]
    return names


# This function can send Slack messages to a channel
def send_webhook_notification(payload_text=None):
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
        bucket_names=getBuckets(),
    )


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
        file = request.files.get("file")
        bucket = request.form.get("bucket")
        if not bucket or bucket == "":
            return "No Bucket Provided", 400
        if not file or file.filename == "":
            return "No File Provided", 400
        content_type = file.mimetype or ""
        if content_type not in ("application/zip", "text/csv"):
            return "Invalid File Type", 400

        data = file.read()
        buf = io.BytesIO(data)
        task_id = str(uuid.uuid4())
        PROGRESS[task_id] = {"pct": 0, "msg": "Starting...", "done": False}
        CURRENT_FILE["name"] = str(file.filename)
        CURRENT_FILE["task_id"] = str(task_id)
        CURRENT_FILE["bucket"] = bucket
        send_webhook_notification(f"Uploading file: {file.filename} -> {bucket}")

        def on_progress(sent: int, total: int):
            try:
                pct = int((sent * 100) / total) if total else 0
                PROGRESS[task_id]["pct"] = pct
                PROGRESS[task_id]["sent"] = sent
                PROGRESS[task_id]["total"] = total
                PROGRESS[task_id]["name"] = file.filename
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
            file_size_mb = len(data) / (1024 * 1024)
            streamer = CANInfluxStreamer(bucket)
            
            try:
                import asyncio

                buf.seek(0)
                uploaded_filename = file.filename or ""

                if content_type == "application/zip":
                    # Use the new automatic method that chooses disk vs memory based on file size
                    asyncio.run(
                        streamer.stream_file_auto(
                            buf, 
                            is_csv=False, 
                            on_progress=on_progress,
                            file_size_mb=file_size_mb
                        )
                    )
                elif content_type == "text/csv":
                    # CSV files always use memory-based processing (they're single files)
                    asyncio.run(
                        streamer.stream_file_auto(
                            buf, 
                            is_csv=True, 
                            csv_filename=uploaded_filename,
                            on_progress=on_progress,
                            file_size_mb=file_size_mb
                        )
                    )
                else:
                    PROGRESS[task_id]["msg"] = "content_type is neither csv nor zip"
                    PROGRESS[task_id]["done"] = True

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
