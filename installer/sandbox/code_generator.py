"""
Code Generation Service - LangGraph multi-step agentic loop for data analysis.
Receives requests from Slackbot, generates code using Anthropic-compatible API, and executes in sandbox.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, Optional, TypedDict

from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS
from anthropic import Anthropic
import requests
from langgraph.graph import StateGraph, END

from mcp_tools import MCPTools

# Load environment variables
load_dotenv()

# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise RuntimeError(
        "ANTHROPIC_API_KEY not found in environment. Add it to your .env or export it as an env var."
    )

ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "MiniMax-M2.7")
SANDBOX_URL = os.getenv("SANDBOX_URL", "http://sandbox-runner:9090")
SANDBOX_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT", "1140"))  # default ~19 min
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))
MAX_STEPS = min(int(os.getenv("MAX_STEPS", "5")), 8)  # hard cap 8
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
ENABLE_MCP = os.getenv("ENABLE_MCP", "true").lower() == "true"

# Configure Anthropic client (supports custom base URL, e.g. MiniMax Anthropic-compatible endpoint)
anthropic_kwargs = {"api_key": ANTHROPIC_API_KEY}
if ANTHROPIC_BASE_URL:
    anthropic_kwargs["base_url"] = ANTHROPIC_BASE_URL
anthropic_client = Anthropic(**anthropic_kwargs)

# Paths
BASE_DIR = Path(__file__).resolve().parent
PROMPT_GUIDE_PATH = BASE_DIR / "prompt-guide.txt"
GENERATED_CODE_PATH = BASE_DIR / "generated_sandbox_code.py"
DATA_DIR = BASE_DIR / "data-downloader"

# ---------------------------------------------------------------------
# Flask App Setup
# ---------------------------------------------------------------------
app = Flask(__name__)
CORS(app)
mcp_tools = MCPTools()

# ---------------------------------------------------------------------
# LangGraph State
# ---------------------------------------------------------------------
class CodeGenState(TypedDict):
    user_prompt: str
    guide: str
    plan: str
    plan_steps: list           # parsed numbered step descriptions
    current_step_index: int    # 0-based index into plan_steps
    step_summaries: list       # [{step, description, ok, output, finding}]
    scratchpad: str            # accumulated findings across steps
    current_code: str
    sandbox_result: dict
    error_message: str
    diagnosis: str
    attempts: int              # attempts for the current step (resets per step)
    retry_info: list
    all_output_files: list     # accumulated output files from all steps
    conclusion: str
    slack_context: Optional[dict]  # {"channel": ..., "thread_ts": ..., "user": ...}
    execution_timeout: int  # sandbox HTTP timeout in seconds
    data_context: str  # pre-scanned sensor list + run windows from data-downloader
    mcp_context: str
    resolved_season: str
    mcp_error: str
    mcp_trace: dict


# ---------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------
def load_prompt_guide() -> str:
    """Reads the prompt guide file."""
    if PROMPT_GUIDE_PATH.exists():
        return PROMPT_GUIDE_PATH.read_text().strip()

    # Minimal fallback if file doesn't exist
    return """You are an expert Python data analyst. Generate clean, executable Python code.
Rules:
- No user input (no input(), sys.stdin)
- Save visualizations to files (plt.savefig())
- Include all necessary imports
- Return only executable code"""


def load_data_context() -> str:
    """
    Build a compact data-context string from the pre-scanned data-downloader JSON files.
    Dynamically discovers all runs_WFR*.json files (WFR24, WFR25, WFR26, ...) sorted
    newest-first. Injects exact sensor lists and run windows so the LLM never needs to
    call discover_sensors() or guess signal names.
    Returns an empty string if the directory is not mounted or no files are found.
    """
    import json as _json

    if not DATA_DIR.exists():
        return ""

    # Discover all seasons present, sorted newest-first (WFR26, WFR25, WFR24, ...)
    season_files = sorted(DATA_DIR.glob("runs_WFR*.json"), reverse=True)
    seasons = []
    for p in season_files:
        m = re.match(r"runs_(WFR\d+)\.json", p.name)
        if m:
            seasons.append(m.group(1))

    if not seasons:
        return ""

    sections = []
    last_updated = "unknown"
    for season in seasons:
        runs_path = DATA_DIR / f"runs_{season}.json"
        sensors_path = DATA_DIR / f"sensors_{season}.json"

        if not runs_path.exists():
            print(f"Warning: runs file missing for {season}, skipping")
            continue

        try:
            runs_data = _json.loads(runs_path.read_text())
        except Exception as e:
            print(f"Warning: could not parse runs for {season}: {e}")
            continue

        last_updated = runs_data.get("updated_at", last_updated)
        runs = runs_data.get("runs", [])

        run_lines = []
        for r in runs:
            note = f" [{r['note']}]" if r.get("note") else ""
            run_lines.append(
                f"  {r['start_local']} → {r['end_local']} (UTC: {r['start_utc']} → {r['end_utc']}, rows: {r['row_count']:,}){note}"
            )

        sensors_section = ""
        if sensors_path.exists():
            try:
                sensors_data = _json.loads(sensors_path.read_text())
                sensors = sensors_data.get("sensors", [])
                sensors_section = f"\n\n=== {season} — All Available Sensors ({len(sensors)} total) ===\n" + ", ".join(sensors)
            except Exception as e:
                print(f"Warning: could not parse sensors for {season}: {e}")
        else:
            print(f"Warning: sensors file missing for {season}, omitting sensor list")

        sections.append(
            f"=== {season} — Available Run Windows ===\n"
            + "\n".join(run_lines)
            + sensors_section
        )

    if not sections:
        return ""

    return (
        f"LIVE DATA CONTEXT (scanned {last_updated}):\n"
        "Use the run windows below to select appropriate start_time/end_time. "
        "Use the sensor list for exact signal names — do NOT invent or guess names.\n\n"
        + "\n\n".join(sections)
    )


def extract_python_code(raw_output: str) -> str:
    """
    Extract ```python ...``` fenced code if present.
    Falls back to raw text if no fence.
    """
    text = raw_output.strip()
    if "```" not in text:
        return text

    segments = text.split("```")
    for idx, segment in enumerate(segments):
        if idx % 2 == 0:
            continue
        stripped = segment.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("python"):
            lines = stripped.splitlines()
            return "\n".join(lines[1:]) if len(lines) > 1 else ""
        return stripped

    return text


def submit_code_to_sandbox(code: str, timeout: int = SANDBOX_TIMEOUT) -> Dict[str, Any]:
    """Submit code to the custom sandbox for execution."""
    try:
        response = requests.post(
            SANDBOX_URL,
            json={"code": code},
            timeout=timeout
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error submitting to sandbox: {e}")
        return {
            "ok": False,
            "std_err": str(e),
            "std_out": "",
            "return_code": -1,
            "output_files": []
        }


def format_error_for_retry(sandbox_result: Dict[str, Any]) -> str:
    """Format sandbox error for retry prompt."""
    error_parts = []

    if sandbox_result.get("std_err"):
        error_parts.append(f"ERROR_TRACE: {sandbox_result['std_err'].strip()}")

    if sandbox_result.get("std_out"):
        error_parts.append(f"OUTPUT: {sandbox_result['std_out'].strip()}")

    return_code = sandbox_result.get("return_code")
    if return_code != 0:
        error_parts.insert(0, f"STATUS: ERROR (return code: {return_code})")

    return "\n".join(error_parts)


def format_sandbox_result(sandbox_result: Dict[str, Any]) -> Dict[str, Any]:
    """Format sandbox result for response."""
    files_info = []
    for file_data in sandbox_result.get("output_files", []):
        file_info = {
            "name": file_data.get("filename"),
            "data": file_data.get("b64_data"),
            "type": "image" if file_data.get("filename", "").endswith((".png", ".jpg", ".jpeg", ".gif", ".svg")) else "file"
        }
        files_info.append(file_info)

    return {
        "status": "success" if sandbox_result.get("ok") else "error",
        "output": sandbox_result.get("std_out", "").strip(),
        "error": sandbox_result.get("std_err", "").strip(),
        "return_code": sandbox_result.get("return_code"),
        "files": files_info
    }


def _extract_text(response) -> str:
    """Extract text from Anthropic response content blocks."""
    return "".join(
        block.text for block in response.content if hasattr(block, "text")
    )


def notify_slack(ctx: Optional[dict], text: str) -> None:
    """Post a progress message to the Slack thread. No-op if context or token is missing."""
    if not ctx or not SLACK_BOT_TOKEN:
        return
    print(f"notify_slack → channel={ctx.get('channel')} thread_ts={ctx.get('thread_ts')} text={text[:60]!r}")
    try:
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            json={
                "channel": ctx["channel"],
                "thread_ts": ctx["thread_ts"],
                "text": text,
            },
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            timeout=5,
        )
        body = resp.json()
        if not body.get("ok"):
            print(f"notify_slack API error: {body.get('error')}")
    except Exception as e:
        print(f"Slack notify failed (non-fatal): {e}")


def parse_plan_steps(plan_text: str) -> list:
    """
    Extract numbered steps from plan text.
    Matches lines like: "1. Do X", "2) Do Y", "3: Do Z"
    Falls back to the entire plan as a single step if no numbered format found.
    Caps at MAX_STEPS.
    """
    steps = []
    for line in plan_text.strip().splitlines():
        m = re.match(r'^\s*\d+[.):\-]\s*(.+)', line.strip())
        if m:
            steps.append(m.group(1).strip())
    return steps[:MAX_STEPS] if steps else [plan_text.strip()[:300]]


# ---------------------------------------------------------------------
# LangGraph Nodes
# ---------------------------------------------------------------------
def mcp_context_node(state: CodeGenState) -> dict:
    """Phase 0: build deterministic season/sensor context through MCP tools."""
    print("--- mcp_context_node ---")

    if not ENABLE_MCP:
        return {
            "mcp_context": "",
            "resolved_season": "",
            "mcp_error": "",
            "mcp_trace": {"enabled": False, "called_tools": []},
        }

    try:
        mcp_result = mcp_tools.build_prompt_context(state["user_prompt"])
        if not mcp_result.get("ok"):
            err = mcp_result.get("error", "MCP context unavailable")
            print(f"MCP context unavailable: {err}")
            return {
                "mcp_context": "",
                "resolved_season": "",
                "mcp_error": err,
                "mcp_trace": {
                    "enabled": True,
                    "called_tools": mcp_result.get("called_tools", []),
                    "error": err,
                },
            }

        resolved_season = str(mcp_result.get("resolved_season", ""))
        print(
            "MCP context built "
            f"(season={resolved_season}, sensors={mcp_result.get('sensors_count', 0)}, runs={mcp_result.get('runs_count', 0)})"
        )
        return {
            "mcp_context": mcp_result.get("context", ""),
            "resolved_season": resolved_season,
            "mcp_error": "",
            "mcp_trace": {
                "enabled": True,
                "resolved_season": resolved_season,
                "resolution_reason": mcp_result.get("resolution_reason"),
                "called_tools": mcp_result.get("called_tools", []),
                "runs_count": mcp_result.get("runs_count", 0),
                "sensors_count": mcp_result.get("sensors_count", 0),
            },
        }
    except Exception as e:
        print(f"MCP context node failed: {e}")
        return {
            "mcp_context": "",
            "resolved_season": "",
            "mcp_error": str(e),
            "mcp_trace": {
                "enabled": True,
                "called_tools": [],
                "error": str(e),
            },
        }


def plan_node(state: CodeGenState) -> dict:
    """Phase 1: decompose the task into numbered steps before generating code."""
    print("--- plan_node ---")
    notify_slack(state.get("slack_context"), "_Planning analysis..._")

    mcp_ctx = state.get("mcp_context", "")
    plan_prompt = state["user_prompt"]
    if mcp_ctx:
        plan_prompt = f"{mcp_ctx}\n\nUSER REQUEST:\n{state['user_prompt']}"

    response = anthropic_client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=800,
        temperature=0.2,
        system=(
            "You are a senior data analyst working with Formula SAE telemetry. "
            "The MCP_CONTEXT above tells you exactly which season, signals, and run windows are available — "
            "use this to write concrete action steps. NEVER ask clarifying questions. "
            "NEVER write steps like 'what signal names are available' — you already have the sensor list. "
            "Break the task into clear, numbered steps (1. 2. 3. etc.). "
            "Each step should be ONE focused operation: fetch specific named signals, compute a metric, or create a visualization. "
            "The LAST step must always produce the visualization or final output. "
            f"Limit to 2-{min(MAX_STEPS, 5)} steps. Focus on WHAT to analyze, not HOW. "
            "Do not specify chunk sizes, query parameters, or implementation details. "
            "Be concise. Do not write code."
        ),
        messages=[{"role": "user", "content": plan_prompt}],
    )
    plan = _extract_text(response)
    plan_steps = parse_plan_steps(plan)
    print(f"Plan ({len(plan_steps)} steps):\n{plan}\n")

    return {
        "plan": plan,
        "plan_steps": plan_steps,
        "current_step_index": 0,
        "step_summaries": [],
        "scratchpad": "",
        "all_output_files": [],
        "attempts": 0,
        "diagnosis": "",
    }


_ROLLING_CHUNK_MINUTES = 30
_FETCH_LIMIT = 20000
_MAX_ASSEMBLED_ROWS = 30000  # cap total rows injected into sandbox scripts (~2MB JSON)


def _fetch_signal_with_rollup(
    mcp_tools: MCPTools,
    season: str,
    signal: str,
    start_utc: str,
    end_utc: str,
) -> tuple[list, list]:
    """
    Fetch a signal over a time window with automatic rolling-window fallback.

    First tries a single large fetch (FETCH_LIMIT rows). If the result is
    exactly FETCH_LIMIT rows the window was truncated — switches to rolling
    CHUNK_MINUTES windows, assembles all chunks, deduplicates by timestamp.

    Returns (rows, bracket_lines).
    """
    bracket_lines: list = []

    def _do_fetch(s: str, e: str, limit: int) -> list:
        res = mcp_tools.query_signal(season=season, signal=signal, start=s, end=e, limit=limit)
        if res.get("ok"):
            return res.get("data", [])
        raise RuntimeError(res.get("error", "query_signal failed"))

    bracket_lines.append(f"[Agent is using query_signal to retrieve {signal} from date {start_utc} to {end_utc}]")
    print(f"  mcp_tools.query_signal({signal}, {start_utc} → {end_utc})")

    rows = _do_fetch(start_utc, end_utc, _FETCH_LIMIT)

    if len(rows) < _FETCH_LIMIT:
        # Not truncated — single fetch was sufficient
        bracket_lines.append(f"[Agent retrieved {len(rows)} readings for {signal} sensor]")
        print(f"  → {len(rows)} rows (single fetch)")
        return rows, bracket_lines

    # Truncated — roll through the window in CHUNK_MINUTES-sized slices
    bracket_lines.append(
        f"[Fetch hit {_FETCH_LIMIT}-row limit; switching to rolling {_ROLLING_CHUNK_MINUTES}-min windows]"
    )
    print(f"  → hit limit ({_FETCH_LIMIT}), rolling window fallback")

    def _parse_utc(ts: str) -> datetime:
        ts = ts.strip().rstrip("Z")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    chunk_start = _parse_utc(start_utc)
    window_end = _parse_utc(end_utc)
    chunk_delta = timedelta(minutes=_ROLLING_CHUNK_MINUTES)

    seen: set = set()
    all_rows: list = []

    while chunk_start < window_end:
        chunk_end = min(chunk_start + chunk_delta, window_end)
        cs = chunk_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        ce = chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            chunk_rows = _do_fetch(cs, ce, _FETCH_LIMIT)
        except Exception as exc:
            print(f"    chunk {cs}→{ce} failed: {exc}")
            chunk_start += chunk_delta
            continue

        added = 0
        for row in chunk_rows:
            if len(all_rows) >= _MAX_ASSEMBLED_ROWS:
                break
            key = row.get("time", "")
            if key not in seen:
                seen.add(key)
                all_rows.append(row)
                added += 1
        print(f"    chunk {cs}→{ce}: {len(chunk_rows)} fetched, {added} new (total {len(all_rows)})")
        if len(all_rows) >= _MAX_ASSEMBLED_ROWS:
            print(f"    hit _MAX_ASSEMBLED_ROWS={_MAX_ASSEMBLED_ROWS}, stopping early")
            break
        chunk_start += chunk_delta

    # Sort by time after assembly
    all_rows.sort(key=lambda r: r.get("time", ""))
    capped = len(all_rows) >= _MAX_ASSEMBLED_ROWS
    bracket_lines.append(
        f"[Agent assembled {len(all_rows)} readings for {signal} sensor via rolling windows"
        + (" (capped)" if capped else "") + "]"
    )
    print(f"  → {len(all_rows)} rows total after rolling assembly")
    return all_rows, bracket_lines


def _strip_data_preamble(code: str) -> str:
    """
    Remove the _RAW_DATA = _json.loads(...) line from generated code before sending
    to the LLM critic — keeps the context window small while preserving analysis logic.
    Replaces that line with a compact placeholder so the critic understands the structure.
    """
    lines = code.splitlines()
    out = []
    for line in lines:
        if line.startswith("_RAW_DATA = _json.loads("):
            out.append("_RAW_DATA = {...}  # pre-injected dict: {signal_name: [row_dicts]}")
        else:
            out.append(line)
    return "\n".join(out)


def _resolve_fetch_spec(step_desc: str, user_prompt: str, mcp_ctx: str) -> dict:
    """
    Ask the LLM to extract a data-fetch spec (signals + UTC time range) from a plan step.
    Returns {"signals": [...], "start": "ISO UTC", "end": "ISO UTC"}.
    Falls back to empty spec on parse failure.
    """
    import json as _json

    response = anthropic_client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=600,
        system=(
            "You are a data query planner. Given a telemetry analysis step and MCP context, "
            "output ONLY valid JSON — no other text:\n"
            '{"signals": ["ExactName1", "ExactName2"], "start": "YYYY-MM-DDTHH:MM:SSZ", "end": "YYYY-MM-DDTHH:MM:SSZ"}\n\n'
            "Rules:\n"
            "- signals: pick 1-4 exact names from sensors_exact_names in MCP_CONTEXT relevant to the step\n"
            "- start/end: pick UTC times from the run with the HIGHEST row_count in MCP_CONTEXT (most data); limit the window to 2 hours max to keep queries fast\n"
            "- If this step is visualization/analysis-only with no new data needed, output: "
            '{"signals": [], "start": "", "end": ""}'
        ),
        messages=[{"role": "user", "content": f"{mcp_ctx}\n\nStep: {step_desc}\nTask: {user_prompt}"}],
    )
    raw = _extract_text(response).strip()
    print(f"  _resolve_fetch_spec stop_reason={response.stop_reason} raw: {raw[:400]}")
    try:
        # Try direct parse first
        spec = _json.loads(raw)
        print(f"  _resolve_fetch_spec parsed directly: signals={spec.get('signals')}")
        return spec
    except Exception:
        pass
    try:
        # Try extracting JSON object (may contain arrays, so allow nested [] but not {})
        m = re.search(r'\{.*?\}', raw, re.DOTALL)
        if m:
            spec = _json.loads(m.group())
            print(f"  _resolve_fetch_spec parsed via regex: signals={spec.get('signals')}")
            return spec
    except Exception as e:
        print(f"  fetch spec parse error: {e} — raw: {raw[:200]}")
    return {"signals": [], "start": "", "end": ""}


def fetch_step_node(state: CodeGenState) -> dict:
    """
    Two-phase step:
      Phase 1 (Python): resolve fetch spec → call mcp_tools.query_signal() directly
      Phase 2 (LLM+sandbox): generate analysis-only code with data pre-injected, execute
    The sandbox never calls data-downloader-api — all data access is done here in Python.
    """
    import json as _json

    step_idx = state["current_step_index"]
    plan_steps = state["plan_steps"]

    if step_idx >= len(plan_steps):
        print(f"--- fetch_step_node: step_idx {step_idx} out of bounds, skipping ---")
        return {}

    step_desc = plan_steps[step_idx]
    attempts = state["attempts"]
    is_last_step = step_idx == len(plan_steps) - 1
    total_steps = len(plan_steps)

    print(f"--- fetch_step_node (step {step_idx + 1}/{total_steps}, attempt {attempts + 1}) ---")

    if attempts == 0:
        notify_slack(state.get("slack_context"), f"_Step {step_idx + 1}/{total_steps}: {step_desc[:60]}..._")
    else:
        notify_slack(state.get("slack_context"), f"_Retrying step {step_idx + 1} (attempt {attempts + 1})..._")

    mcp_ctx = state.get("mcp_context", "")
    resolved_season = state.get("resolved_season", "")
    scratchpad = state.get("scratchpad", "")
    diagnosis = state.get("diagnosis", "")

    # ------------------------------------------------------------------
    # Phase 1: Fetch data via mcp_tools (Python level, not sandbox code)
    # ------------------------------------------------------------------
    spec = _resolve_fetch_spec(step_desc, state["user_prompt"], mcp_ctx)
    signals = spec.get("signals") or []
    start_utc = spec.get("start", "")
    end_utc = spec.get("end", "")

    fetched_data: Dict[str, list] = {}
    bracket_lines: list = []

    for signal in signals:
        if not signal or not start_utc or not end_utc:
            continue
        try:
            rows, sig_brackets = _fetch_signal_with_rollup(
                mcp_tools, resolved_season, signal, start_utc, end_utc
            )
            fetched_data[signal] = rows
            bracket_lines.extend(sig_brackets)
        except Exception as fetch_exc:
            err = str(fetch_exc)
            bracket_lines.append(f"[Agent failed to retrieve {signal}: {err}]")
            print(f"  → failed: {err}")

    data_summary = (
        ", ".join(f"{sig}: {len(rows)} rows" for sig, rows in fetched_data.items())
        if fetched_data else "no signals fetched for this step"
    )
    print(f"  Data summary: {data_summary}")

    # Serialize fetched data for injection into sandbox code
    data_json_str = _json.dumps(fetched_data)

    # Preamble injected at top of every generated script
    # _RAW_DATA: {signal_name: [{"time": ISO, signal_name: value, ...}, ...]}
    data_preamble = (
        "import json as _json, pandas as _pd, matplotlib, traceback\n"
        "matplotlib.use('Agg')\n"
        f"_RAW_DATA = _json.loads({repr(data_json_str)})\n"
        "# _RAW_DATA keys are signal names; each value is a list of row dicts from InfluxDB wide schema.\n"
        "# Timestamps are ISO 8601 UTC strings (microsecond precision). Build a DataFrame per signal:\n"
        "#   df = _pd.DataFrame(_RAW_DATA['SignalName'])\n"
        "#   df['time'] = _pd.to_datetime(df['time'], utc=True).dt.tz_convert('America/Toronto')\n"
        "#   df = df.set_index('time').sort_index()\n"
    )
    bracket_prints = "\n".join(f"print({repr(line)})" for line in bracket_lines)

    # ------------------------------------------------------------------
    # Phase 2: Generate analysis/visualization code (no data fetching)
    # ------------------------------------------------------------------
    output_instruction = (
        "This is the FINAL step. Save your visualization to output.png and print a brief summary."
        if is_last_step else
        "This is an intermediate analysis step. Print key statistics and findings. Do NOT call plt.savefig()."
    )

    if diagnosis:
        code_prompt = (
            f"Data already fetched ({data_summary}).\n\n"
            + (f"PREVIOUS FINDINGS:\n{scratchpad[:1500]}\n\n" if scratchpad else "")
            + f"STEP: {step_desc}\n{output_instruction}\n\n"
            + f"PREVIOUS ATTEMPT FAILED:\n{diagnosis}\n\n"
            + "Fix the code. Do not call any HTTP APIs — data is already in _RAW_DATA."
        )
    else:
        code_prompt = (
            f"Data already fetched ({data_summary}).\n\n"
            + (f"PREVIOUS FINDINGS:\n{scratchpad[:1500]}\n\n" if scratchpad else "")
            + f"STEP: {step_desc}\n{output_instruction}\n\n"
            + f"TASK: {state['user_prompt']}"
        )

    # Build a compact preamble description — never send the full JSON to the LLM
    # (it can be 100KB+ and exceeds the model's context window)
    preamble_summary_lines = [
        "import json as _json, pandas as _pd, matplotlib, traceback",
        "matplotlib.use('Agg')",
        "# _RAW_DATA is pre-injected as a Python literal — do NOT redefine it.",
        "# _RAW_DATA structure: {signal_name: [row_dict, ...]}",
        "# Each row_dict has 'time' (ISO UTC microsecond string) and signal value columns.",
        "# Use: pd.to_datetime(df['time'], utc=True) to parse timestamps.",
    ]
    for sig, rows in fetched_data.items():
        sample_val = rows[0].get(sig) if rows else "N/A"
        preamble_summary_lines.append(
            f"# _RAW_DATA['{sig}']: {len(rows)} rows, e.g. {{'time': '{rows[0]['time'] if rows else ''}', '{sig}': {sample_val}}}"
        )
    preamble_summary = "\n".join(preamble_summary_lines)

    response = anthropic_client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=4000,
        system=(
            "You are an expert Python data analyst working with Formula SAE telemetry. "
            "Return only executable Python code — no commentary, no markdown fences.\n\n"
            "The data has already been fetched and is available in _RAW_DATA (a dict injected before your code). "
            "DO NOT call requests, urllib, or any HTTP API — data access is complete.\n"
            "DO NOT import requests.\n\n"
            "_RAW_DATA format: {signal_name: [row_dict, ...]} where each row_dict has 'time' (ISO UTC string) "
            "and signal columns from InfluxDB wide schema.\n\n"
            "CRITICAL: In every except block, always call traceback.print_exc() then re-raise with `raise`. "
            "Never silently swallow exceptions — always re-raise so the sandbox reports a non-zero exit code. "
            "This is debug mode — verbose errors and non-zero exit on failure are required.\n\n"
            "If _RAW_DATA is empty or a signal has 0 rows, print a clear message and handle gracefully."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"Preamble already prepended to your code (do not repeat or redefine _RAW_DATA):\n"
                f"```python\n{preamble_summary}\n```\n\n"
                + code_prompt
            ),
        }],
    )

    analysis_code = extract_python_code(_extract_text(response))

    # Assemble full script: preamble + bracket prints + analysis
    full_code = data_preamble + "\n" + bracket_prints + ("\n\n" if bracket_prints else "") + analysis_code
    GENERATED_CODE_PATH.write_text(full_code, encoding="utf-8")
    print(f"Generated analysis code for step {step_idx + 1} (data: {len(data_json_str)} bytes embedded)")

    sandbox_result = submit_code_to_sandbox(full_code, timeout=state.get("execution_timeout", SANDBOX_TIMEOUT))
    error_message = "" if sandbox_result.get("ok") else format_error_for_retry(sandbox_result)

    return {
        "current_code": full_code,
        "sandbox_result": sandbox_result,
        "error_message": error_message,
        "attempts": attempts + 1,
    }


def analyze_step_node(state: CodeGenState) -> dict:
    """Examine step result: update scratchpad and decide routing."""
    step_idx = state["current_step_index"]
    plan_steps = state["plan_steps"]
    step_desc = plan_steps[step_idx] if step_idx < len(plan_steps) else ""
    sandbox_ok = state["sandbox_result"].get("ok", False)
    stdout = state["sandbox_result"].get("std_out", "").strip()

    print(f"--- analyze_step_node (step {step_idx + 1}, ok={sandbox_ok}, attempts={state['attempts']}) ---")

    if not sandbox_ok:
        if state["attempts"] <= MAX_RETRIES:
            # Diagnose and retry
            notify_slack(state.get("slack_context"), f"_Step {step_idx + 1} failed, diagnosing..._")
            response = anthropic_client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=800,
                temperature=0.1,
                system=(
                    "You are a senior Python code reviewer for a Formula SAE telemetry sandbox. "
                    "Data has already been fetched and pre-injected as _RAW_DATA at the top of the script — "
                    "the sandbox code must NOT call requests or any HTTP API. "
                    "Focus on fixing pandas, matplotlib, or logic errors in the analysis code. "
                    "Be concise and specific."
                ),
                messages=[{
                    "role": "user",
                    "content": (
                        f"Code (analysis portion only — _RAW_DATA preamble omitted):\n"
                        f"```python\n{_strip_data_preamble(state['current_code'])}\n```\n\n"
                        f"Error:\n{state['error_message']}\n\n"
                        "Explain:\n1. Root cause\n2. What needs to change\n3. Specific fix strategy"
                    ),
                }],
            )
            diagnosis = _extract_text(response)
            print(f"Diagnosis:\n{diagnosis}\n")

            return {
                "diagnosis": diagnosis,
                "retry_info": state["retry_info"] + [{
                    "step": step_idx + 1,
                    "attempt": state["attempts"],
                    "error": state["error_message"],
                    "diagnosis": diagnosis,
                }],
            }
        else:
            # Max retries exhausted — record failure and advance
            notify_slack(state.get("slack_context"), f"_Step {step_idx + 1} failed after {state['attempts']} attempts, moving on..._")
            step_summary = {
                "step": step_idx + 1,
                "description": step_desc,
                "ok": False,
                "output": state["error_message"][:500],
                "finding": f"Step failed after {state['attempts']} attempts.",
            }
            updated_scratchpad = state.get("scratchpad", "")
            if updated_scratchpad:
                updated_scratchpad += "\n\n"
            updated_scratchpad += f"Step {step_idx + 1} ({step_desc}): FAILED — {state['error_message'][:200]}"
            # Cap scratchpad at 4000 chars, keeping the tail (most recent findings)
            if len(updated_scratchpad) > 4000:
                updated_scratchpad = updated_scratchpad[-4000:]

            return {
                "step_summaries": state["step_summaries"] + [step_summary],
                "scratchpad": updated_scratchpad,
                "current_step_index": step_idx + 1,
                "attempts": 0,
                "diagnosis": "",
            }

    # Step succeeded — summarize findings
    notify_slack(state.get("slack_context"), f"_Step {step_idx + 1} complete ✓_")

    # Include any stderr (warnings, tracebacks that didn't cause exit ≠ 0) in output
    stderr = state["sandbox_result"].get("std_err", "").strip()
    combined_output = stdout
    if stderr:
        combined_output = (stdout + "\n--- STDERR ---\n" + stderr).strip()

    # Lightweight LLM summary of findings
    if combined_output:
        response = anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=300,
            temperature=0.1,
            system="Summarize the data analysis output in 1-2 concise sentences. Focus on key numerical values and findings. No code.",
            messages=[{
                "role": "user",
                "content": f"Step goal: {step_desc}\nOutput:\n{combined_output[:2000]}",
            }],
        )
        finding = _extract_text(response)
    else:
        finding = "Step completed with no printed output."

    step_summary = {
        "step": step_idx + 1,
        "description": step_desc,
        "ok": True,
        "output": combined_output[:1000],
        "finding": finding,
    }

    # Accumulate scratchpad
    updated_scratchpad = state.get("scratchpad", "")
    if updated_scratchpad:
        updated_scratchpad += "\n\n"
    updated_scratchpad += f"Step {step_idx + 1} ({step_desc}): {finding}"
    if len(updated_scratchpad) > 4000:
        updated_scratchpad = updated_scratchpad[-4000:]

    # Collect output files from this step
    new_files = list(state.get("all_output_files", []))
    for f in state["sandbox_result"].get("output_files", []):
        new_files.append(f)

    return {
        "step_summaries": state["step_summaries"] + [step_summary],
        "scratchpad": updated_scratchpad,
        "current_step_index": step_idx + 1,
        "attempts": 0,
        "diagnosis": "",
        "all_output_files": new_files,
    }


def conclude_node(state: CodeGenState) -> dict:
    """Synthesize findings from all steps into a final analysis summary."""
    print("--- conclude_node ---")
    notify_slack(state.get("slack_context"), "_Summarising findings..._")

    scratchpad = state.get("scratchpad", "").strip()
    step_summaries = state.get("step_summaries", [])
    successful_steps = [s for s in step_summaries if s.get("ok")]

    if not successful_steps and not scratchpad:
        return {"conclusion": "Analysis could not be completed — all steps failed."}

    # Include final step stdout for context if available
    final_stdout = state["sandbox_result"].get("std_out", "").strip()
    content = (
        f"Task:\n{state['user_prompt']}\n\n"
        f"Step-by-step findings:\n{scratchpad[:3000]}"
        + (f"\n\nFinal step output:\n{final_stdout[:1000]}" if final_stdout else "")
    )

    response = anthropic_client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=600,
        temperature=0.3,
        system=(
            "You are a Formula SAE data engineer. Given analysis findings from multiple steps, "
            "write a concise summary: 2-3 sentences of key findings, then bullet-point "
            "recommendations for further investigation. Be specific and actionable. No code.\n"
            "Format for Slack mrkdwn: use *bold* (single asterisk) not **bold**, "
            "use _italic_ (single underscore), use - for bullets, no ## headers."
        ),
        messages=[{"role": "user", "content": content}],
    )
    conclusion = _extract_text(response)
    print(f"Conclusion:\n{conclusion}\n")
    return {"conclusion": conclusion}


def route_after_analyze(state: CodeGenState) -> str:
    """Conditional edge: retry current step, advance to next, or conclude."""
    # Retry: diagnosis set, within retry budget
    if state.get("diagnosis") and state["attempts"] <= MAX_RETRIES:
        return "fetch_step"
    # More steps remaining
    if state["current_step_index"] < len(state["plan_steps"]):
        return "fetch_step"
    # All steps done (or last step failed past retries)
    return "conclude"


# ---------------------------------------------------------------------
# Build LangGraph
# ---------------------------------------------------------------------
_workflow = StateGraph(CodeGenState)
_workflow.add_node("mcp_context", mcp_context_node)
_workflow.add_node("plan", plan_node)
_workflow.add_node("fetch_step", fetch_step_node)
_workflow.add_node("analyze_step", analyze_step_node)
_workflow.add_node("conclude", conclude_node)

_workflow.set_entry_point("mcp_context")
_workflow.add_edge("mcp_context", "plan")
_workflow.add_edge("plan", "fetch_step")
_workflow.add_edge("fetch_step", "analyze_step")
_workflow.add_conditional_edges("analyze_step", route_after_analyze, {
    "fetch_step": "fetch_step",
    "conclude": "conclude",
})
_workflow.add_edge("conclude", END)

graph = _workflow.compile()


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "code-generator"})


@app.route('/api/mcp/health', methods=['GET'])
def mcp_health():
    """MCP bridge health endpoint."""
    status = "ok"
    error = ""
    seasons = []
    if ENABLE_MCP:
        try:
            seasons_result = mcp_tools.list_seasons()
            if not seasons_result.get("ok"):
                status = "degraded"
                error = seasons_result.get("error", "unknown")
            seasons = seasons_result.get("seasons", [])
        except Exception as e:
            status = "degraded"
            error = str(e)

    return jsonify({
        "status": status,
        "enabled": ENABLE_MCP,
        "service": "code-generator-mcp-bridge",
        "tools": [t["name"] for t in mcp_tools.list_tools()],
        "season_count": len(seasons),
        "error": error,
    })


@app.route('/api/mcp/tools', methods=['GET'])
def mcp_list_tools():
    """Return available MCP tools and input schemas."""
    return jsonify({"tools": mcp_tools.list_tools(), "enabled": ENABLE_MCP})


@app.route('/api/mcp/call', methods=['POST'])
def mcp_call_tool():
    """Call an MCP tool through HTTP for deterministic data operations."""
    if not ENABLE_MCP:
        return jsonify({"ok": False, "error": "MCP bridge disabled (ENABLE_MCP=false)"}), 503

    payload = request.get_json(silent=True) or {}
    tool_name = str(payload.get("tool", "")).strip()
    arguments = payload.get("arguments") or {}

    if not tool_name:
        return jsonify({"ok": False, "error": "'tool' is required"}), 400
    if not isinstance(arguments, dict):
        return jsonify({"ok": False, "error": "'arguments' must be an object"}), 400

    try:
        result = mcp_tools.call(tool_name, arguments)
        status = 200 if result.get("ok", True) else 400
        return jsonify(result), status
    except requests.HTTPError as e:
        return jsonify({"ok": False, "error": f"Upstream API error: {e}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/generate-code', methods=['POST'])
def generate_code():
    """Generate and execute Python code based on user prompt with multi-step LangGraph orchestration."""
    try:
        data = request.get_json()
        user_prompt = data.get('prompt', '').strip()
        slack_context = data.get('slack_context')  # optional: {"channel", "thread_ts", "user"}
        execution_timeout = int(data.get('execution_timeout', SANDBOX_TIMEOUT))

        if not user_prompt:
            return jsonify({"error": "Prompt is required"}), 400

        guide = load_prompt_guide()
        data_context = load_data_context()
        if data_context:
            print(f"Data context loaded ({len(data_context)} chars)")
        else:
            print("Warning: data context not available (data-downloader not mounted?)")

        initial_state: CodeGenState = {
            "user_prompt": user_prompt,
            "guide": guide,
            "plan": "",
            "plan_steps": [],
            "current_step_index": 0,
            "step_summaries": [],
            "scratchpad": "",
            "current_code": "",
            "sandbox_result": {},
            "error_message": "",
            "diagnosis": "",
            "attempts": 0,
            "retry_info": [],
            "all_output_files": [],
            "conclusion": "",
            "slack_context": slack_context,
            "execution_timeout": execution_timeout,
            "data_context": data_context,
            "mcp_context": "",
            "resolved_season": "",
            "mcp_error": "",
            "mcp_trace": {},
        }

        final_state = graph.invoke(initial_state)

        # Build result from last sandbox execution
        result = format_sandbox_result(final_state["sandbox_result"])

        # Merge all_output_files from all steps into the result files.
        # Deduplicate by filename, keeping the latest version (last in list wins).
        all_files_raw = final_state.get("all_output_files", [])
        if all_files_raw:
            seen: dict = {}
            for f in all_files_raw:
                fname = f.get("filename", "")
                seen[fname] = {
                    "name": fname,
                    "data": f.get("b64_data"),
                    "type": "image" if fname.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg")) else "file",
                }
            result["files"] = list(seen.values())

        # If any step succeeded, mark overall status as success
        step_summaries = final_state.get("step_summaries", [])
        if any(s.get("ok") for s in step_summaries):
            result["status"] = "success"

        response_body = {"code": final_state["current_code"], "result": result}

        if step_summaries:
            response_body["step_summaries"] = step_summaries

        if final_state.get("conclusion"):
            response_body["conclusion"] = final_state["conclusion"]

        if final_state["retry_info"]:
            response_body["retries"] = final_state["retry_info"]

        if final_state.get("resolved_season"):
            response_body["resolved_season"] = final_state["resolved_season"]

        if final_state.get("mcp_error"):
            response_body["mcp_warning"] = final_state["mcp_error"]

        if final_state.get("mcp_trace"):
            response_body["mcp_trace"] = final_state["mcp_trace"]

        failed_steps = [s for s in step_summaries if not s.get("ok")]
        if failed_steps and len(failed_steps) == len(step_summaries):
            response_body["max_retries_reached"] = True

        return jsonify(response_body)

    except Exception as e:
        print(f"Error in generate_code: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "error": str(e),
            "code": None,
            "result": {
                "status": "error",
                "error": str(e),
                "output": "",
                "files": []
            }
        }), 500


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main():
    """Start the code generation service."""
    port = int(os.getenv("CODE_GEN_PORT", "3030"))
    debug = os.getenv("DEBUG", "false").lower() == "true"

    print(f"Starting code generation service on http://0.0.0.0:{port}")
    print(f"Anthropic Model: {ANTHROPIC_MODEL}")
    if ANTHROPIC_BASE_URL:
        print(f"Anthropic Base URL: {ANTHROPIC_BASE_URL}")
    print(f"Sandbox URL: {SANDBOX_URL}")
    print(f"MCP Bridge: {'enabled' if ENABLE_MCP else 'disabled'}")
    print(f"Max Retries: {MAX_RETRIES}")
    print(f"Max Steps: {MAX_STEPS}")
    print(f"Slack notifications: {'enabled' if SLACK_BOT_TOKEN else 'disabled (no SLACK_BOT_TOKEN)'}")

    app.run(host='0.0.0.0', port=port, debug=debug)


if __name__ == "__main__":
    main()
