from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    stream_with_context,
    Response,
)
import uuid, time, threading, json, logging, requests, os, asyncio
from typing import Optional, Tuple, List
from urllib.parse import quote
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
# information_schema / catalog tables — not user telemetry seasons
_INFLUX_SYSTEM_TABLE_NAMES = frozenset(
    {
        "views",
        "tables",
        "schemata",
        "routines",
        "queries",
        "processing_engine_triggers",
        "processing_engine_trigger_arguments",
        "processing_engine_logs",
        "parquet_files",
        "parameters",
        "last_caches",
        "influxdb_schema",
        "distinct_caches",
        "df_settings",
        "columns",
    }
)
GITHUB_DBC_TOKEN = os.getenv("GITHUB_DBC_TOKEN", "").strip()
GITHUB_DBC_REPO = os.getenv("GITHUB_DBC_REPO", "Western-Formula-Racing/DBC").strip()
GITHUB_DBC_BRANCH = os.getenv("GITHUB_DBC_BRANCH", "main").strip()
app = Flask(__name__)


def _github_repo_parts() -> Tuple[str, str]:
    owner, slash, repo = GITHUB_DBC_REPO.partition("/")
    if not slash or not owner or not repo:
        raise ValueError("GITHUB_DBC_REPO must be owner/repo")
    return owner, repo


def _github_headers() -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_DBC_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_DBC_TOKEN}"
    return h


def list_github_dbc_paths() -> Tuple[List[str], Optional[str]]:
    """
    List .dbc blob paths under GITHUB_DBC_REPO at GITHUB_DBC_BRANCH (recursive tree).
    Returns (paths_sorted, error_message_or_none).
    """
    if not GITHUB_DBC_TOKEN:
        return [], None
    try:
        owner, repo = _github_repo_parts()
    except ValueError as e:
        return [], str(e)
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{GITHUB_DBC_BRANCH}?recursive=1"
    try:
        r = requests.get(url, headers=_github_headers(), timeout=20)
        if r.status_code != 200:
            return [], f"GitHub tree {r.status_code}: {r.text[:300]}"
        tree = r.json().get("tree") or []
        paths = [
            x["path"]
            for x in tree
            if x.get("type") == "blob" and str(x.get("path", "")).lower().endswith(".dbc")
        ]
        return sorted(paths), None
    except requests.RequestException as e:
        return [], str(e)


def download_github_dbc_to_temp(repo_path: str) -> str:
    """Download a repo-relative .dbc path to a temp file; return filesystem path."""
    owner, repo = _github_repo_parts()
    enc = quote(repo_path, safe="")
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{enc}?ref={GITHUB_DBC_BRANCH}"
    r = requests.get(
        url,
        headers={**_github_headers(), "Accept": "application/vnd.github.raw"},
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"GitHub download {r.status_code}: {r.text[:400]}")
    import tempfile

    fd, tmp = tempfile.mkstemp(suffix=".dbc")
    try:
        os.write(fd, r.content)
    finally:
        os.close(fd)
    return tmp


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _seasons_from_env() -> list[str]:
    """Fallback: parse SEASONS env var (format: 'WFR25:2025,WFR26:2026')."""
    raw = os.getenv("SEASONS", "")
    seasons = [part.split(":")[0].strip() for part in raw.split(",") if part.strip()]
    return sorted(seasons, reverse=True) if seasons else ["WFR26", "WFR25"]


def _table_create_conflict(response: requests.Response) -> bool:
    """True if Influx rejected create because the table already exists (idempotent Add Season)."""
    if response.status_code not in (400, 409):
        return False
    lowered = response.text.lower()
    if any(s in lowered for s in ("already exists", "already exist", "duplicate")):
        return True
    try:
        data = response.json()
        err = str(data.get("error", "")).lower()
        if any(s in err for s in ("already exists", "already exist", "duplicate")):
            return True
    except Exception:
        pass
    return False


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
        seasons = []
        for row in res.json():
            name = row.get("table_name") or ""
            if not name or name.startswith("_"):
                continue
            if name.lower() in _INFLUX_SYSTEM_TABLE_NAMES:
                continue
            seasons.append(name)
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


@app.route("/dbc/list", methods=["GET"])
def dbc_list():
    """List .dbc files from the configured GitHub repo (token never exposed to the client)."""
    if not GITHUB_DBC_TOKEN:
        return jsonify(
            {
                "token_configured": False,
                "items": [],
                "message": "GITHUB_DBC_TOKEN is not set; using optional custom upload or container default DBC.",
            }
        )
    paths, err = list_github_dbc_paths()
    if err:
        error_logger.warning("dbc_list GitHub error: %s", err)
        return jsonify({"token_configured": True, "items": [], "error": err})
    return jsonify({"token_configured": True, "items": paths, "error": None})


@app.route("/create-bucket", methods=["POST"])
def create_bucket():
    """Create a new table (season) inside INFLUXDB_DATABASE, not a new InfluxDB database."""
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"error": "No bucket name provided"}), 400
    if len(name) > 256:
        return jsonify({"error": "Name too long (max 256 characters)"}), 400

    api_url = f"{INFLUXDB_URL.rstrip('/')}/api/v3/configure/table"
    # Tags must match CANInfluxStreamer line protocol (helper._parse_row_generator).
    payload = {
        "db": INFLUXDB_DATABASE,
        "table": name,
        "tags": ["messageName", "canId"],
        "fields": [],
    }
    res = requests.post(
        api_url,
        headers={"Authorization": f"Token {INFLUXDB_TOKEN}", "Content-Type": "application/json"},
        json=payload,
        timeout=10,
    )
    if res.status_code in (200, 201, 204):
        return jsonify({"name": name})
    if _table_create_conflict(res):
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

        dbc_github_path = (request.form.get("dbc_github_path") or "").strip()
        dbc_temp_path = None
        dbc_file = request.files.get("dbc")
        team_paths, _team_err = list_github_dbc_paths()
        token_on = bool(GITHUB_DBC_TOKEN)

        if token_on:
            if dbc_github_path:
                if dbc_github_path not in team_paths:
                    return jsonify({"error": "Invalid or unknown team DBC path; refresh the page and pick again."}), 400
                try:
                    dbc_temp_path = download_github_dbc_to_temp(dbc_github_path)
                except Exception as e:
                    error_logger.error(e)
                    return jsonify({"error": f"Could not download DBC from GitHub: {e}"}), 400
                error_logger.info("DBC from GitHub: %s -> %s", dbc_github_path, dbc_temp_path)
            elif dbc_file and dbc_file.filename:
                if not dbc_file.filename.lower().endswith(".dbc"):
                    return jsonify({"error": "Invalid DBC file type. Only .dbc files allowed."}), 400
                import tempfile

                with tempfile.NamedTemporaryFile(delete=False, suffix=".dbc") as tmp:
                    dbc_file.save(tmp)
                    dbc_temp_path = tmp.name
                error_logger.info("Custom DBC uploaded: %s -> %s", dbc_file.filename, dbc_temp_path)
            else:
                if len(team_paths) >= 1:
                    return (
                        jsonify(
                            {
                                "error": "Select a team DBC from the list or upload a custom .dbc file.",
                            }
                        ),
                        400,
                    )
                return (
                    jsonify(
                        {
                            "error": "No .dbc files found in the team repo; upload a custom .dbc file.",
                        }
                    ),
                    400,
                )
        else:
            if dbc_github_path:
                return jsonify({"error": "GitHub DBC is not configured on this server."}), 400
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
