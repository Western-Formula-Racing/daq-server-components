"""
Code Generation Service - LangGraph orchestrator for Anthropic/MiniMax + Sandbox execution.
Receives requests from Slackbot, generates code using Anthropic-compatible API, and executes in sandbox.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any, Optional, TypedDict

from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS
from anthropic import Anthropic
import requests
from langgraph.graph import StateGraph, END

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
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")

# Cohere implementation kept for quick rollback:
# COHERE_API_KEY = os.getenv("COHERE_API_KEY")
# if not COHERE_API_KEY:
#     raise RuntimeError(
#         "COHERE_API_KEY not found in environment. Add it to your .env or export it as an env var."
#     )
#
# COHERE_MODEL = os.getenv("COHERE_MODEL", "command-a-reasoning-08-2025")
# co = cohere.Client(COHERE_API_KEY)

# Configure Anthropic client (supports custom base URL, e.g. MiniMax Anthropic-compatible endpoint)
anthropic_kwargs = {"api_key": ANTHROPIC_API_KEY}
if ANTHROPIC_BASE_URL:
    anthropic_kwargs["base_url"] = ANTHROPIC_BASE_URL
anthropic_client = Anthropic(**anthropic_kwargs)

# Paths
BASE_DIR = Path(__file__).resolve().parent
PROMPT_GUIDE_PATH = BASE_DIR / "prompt-guide.txt"
GENERATED_CODE_PATH = BASE_DIR / "generated_sandbox_code.py"

# ---------------------------------------------------------------------
# Flask App Setup
# ---------------------------------------------------------------------
app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------
# LangGraph State
# ---------------------------------------------------------------------
class CodeGenState(TypedDict):
    user_prompt: str
    guide: str
    plan: str
    current_code: str
    sandbox_result: dict
    error_message: str
    diagnosis: str
    attempts: int
    retry_info: list
    conclusion: str
    slack_context: Optional[dict]  # {"channel": ..., "thread_ts": ..., "user": ...}


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


def submit_code_to_sandbox(code: str) -> Dict[str, Any]:
    """Submit code to the custom sandbox for execution."""
    try:
        response = requests.post(
            SANDBOX_URL,
            json={"code": code},
            timeout=60
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


# ---------------------------------------------------------------------
# LangGraph Nodes
# ---------------------------------------------------------------------
def plan_node(state: CodeGenState) -> dict:
    """Phase 1: decompose the task into numbered steps before generating code."""
    print("--- plan_node ---")
    notify_slack(state.get("slack_context"), "_Planning analysis..._")

    response = anthropic_client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=800,
        temperature=0.2,
        system=(
            "You are a senior data analyst working with Formula SAE telemetry. "
            "Break the task into clear, numbered steps focused on WHAT to analyze, not HOW. "
            "Do not specify chunk sizes, time windows, query parameters, or any implementation details — "
            "those are determined by the technical implementation guide. Be concise. Do not write code."
        ),
        messages=[{"role": "user", "content": state["user_prompt"]}],
    )
    plan = _extract_text(response)
    print(f"Plan:\n{plan}\n")
    return {"plan": plan}


def generate_node(state: CodeGenState) -> dict:
    """Phase 2: generate Python code using the plan (first attempt) or diagnosis (retries)."""
    attempt = state["attempts"]
    print(f"--- generate_node (attempt {attempt + 1}) ---")

    if attempt == 0:
        notify_slack(state.get("slack_context"), "_Generating code..._")
        full_prompt = (
            f"{state['guide']}\n\n"
            f"PLAN:\n{state['plan']}\n\n"
            f"TASK:\n{state['user_prompt']}"
        )
    else:
        notify_slack(state.get("slack_context"), f"_Regenerating code (attempt {attempt + 1})..._")
        full_prompt = (
            f"Original Task:\n{state['user_prompt']}\n\n"
            f"Previous Plan:\n{state['plan']}\n\n"
            f"Failure Analysis:\n{state['diagnosis']}\n\n"
            "Fix the code accordingly. Do not repeat the same mistake."
        )

    response = anthropic_client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=4000,
        temperature=0.2,
        system="You are an expert Python data analyst. Return only executable Python code.",
        messages=[{"role": "user", "content": full_prompt}],
    )

    python_code = extract_python_code(_extract_text(response))
    GENERATED_CODE_PATH.write_text(python_code, encoding="utf-8")
    print(f"Generated code written to {GENERATED_CODE_PATH}")

    return {"current_code": python_code, "attempts": attempt + 1}


def execute_node(state: CodeGenState) -> dict:
    """Phase 3: run the generated code in the sandbox."""
    print("--- execute_node ---")
    notify_slack(state.get("slack_context"), "_Executing code..._")

    sandbox_result = submit_code_to_sandbox(state["current_code"])
    error_message = "" if sandbox_result.get("ok") else format_error_for_retry(sandbox_result)

    return {"sandbox_result": sandbox_result, "error_message": error_message}


def critic_node(state: CodeGenState) -> dict:
    """Phase 4 (on failure): diagnose root cause and suggest a concrete fix strategy."""
    print("--- critic_node ---")
    notify_slack(state.get("slack_context"), "_Analysing failure, preparing fix..._")

    response = anthropic_client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=800,
        temperature=0.1,
        system="You are a senior Python code reviewer. Be concise and specific.",
        messages=[{
            "role": "user",
            "content": (
                f"Code:\n```python\n{state['current_code']}\n```\n\n"
                f"Error:\n{state['error_message']}\n\n"
                "Explain:\n1. Root cause\n2. What needs to change\n3. Specific fix strategy"
            ),
        }],
    )
    diagnosis = _extract_text(response)
    print(f"Diagnosis:\n{diagnosis}\n")

    updated_retry_info = state["retry_info"] + [{
        "attempt": state["attempts"],
        "error": state["error_message"],
        "diagnosis": diagnosis,
    }]
    return {"diagnosis": diagnosis, "retry_info": updated_retry_info}


def conclude_node(state: CodeGenState) -> dict:
    """Phase 5 (on success): synthesize findings and provide engineering recommendations."""
    print("--- conclude_node ---")
    notify_slack(state.get("slack_context"), "_Summarising findings..._")

    stdout = state["sandbox_result"].get("std_out", "").strip()
    response = anthropic_client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=600,
        temperature=0.3,
        system=(
            "You are a Formula SAE data engineer. Given a data analysis task and its output, "
            "write a concise engineering summary: 2-3 sentences of key findings, then bullet-point "
            "recommendations for further investigation. Be specific and actionable. No code."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"Task:\n{state['user_prompt']}\n\n"
                f"Analysis output:\n{stdout[:3000]}"
            ),
        }],
    )
    conclusion = _extract_text(response)
    print(f"Conclusion:\n{conclusion}\n")
    return {"conclusion": conclusion}


def route_after_execute(state: CodeGenState) -> str:
    """Conditional edge: conclude on success, critic on failure with retries, else end."""
    if state["sandbox_result"].get("ok"):
        return "conclude"
    if state["attempts"] < MAX_RETRIES + 1:
        return "critic"
    return "end"


# ---------------------------------------------------------------------
# Build LangGraph
# ---------------------------------------------------------------------
_workflow = StateGraph(CodeGenState)
_workflow.add_node("plan", plan_node)
_workflow.add_node("generate", generate_node)
_workflow.add_node("execute", execute_node)
_workflow.add_node("critic", critic_node)
_workflow.add_node("conclude", conclude_node)

_workflow.set_entry_point("plan")
_workflow.add_edge("plan", "generate")
_workflow.add_edge("generate", "execute")
_workflow.add_conditional_edges("execute", route_after_execute, {
    "conclude": "conclude",
    "critic": "critic",
    "end": END,
})
_workflow.add_edge("conclude", END)
_workflow.add_edge("critic", "generate")

graph = _workflow.compile()


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "code-generator"})


@app.route('/api/generate-code', methods=['POST'])
def generate_code():
    """Generate and execute Python code based on user prompt with LangGraph orchestration."""
    try:
        data = request.get_json()
        user_prompt = data.get('prompt', '').strip()
        slack_context = data.get('slack_context')  # optional: {"channel", "thread_ts", "user"}

        if not user_prompt:
            return jsonify({"error": "Prompt is required"}), 400

        guide = load_prompt_guide()

        initial_state: CodeGenState = {
            "user_prompt": user_prompt,
            "guide": guide,
            "plan": "",
            "current_code": "",
            "sandbox_result": {},
            "error_message": "",
            "diagnosis": "",
            "attempts": 0,
            "retry_info": [],
            "conclusion": "",
            "slack_context": slack_context,
        }

        final_state = graph.invoke(initial_state)
        result = format_sandbox_result(final_state["sandbox_result"])

        response_body = {"code": final_state["current_code"], "result": result}
        if final_state.get("conclusion"):
            response_body["conclusion"] = final_state["conclusion"]
        if final_state["retry_info"]:
            response_body["retries"] = final_state["retry_info"]
        if not final_state["sandbox_result"].get("ok") and final_state["attempts"] >= MAX_RETRIES + 1:
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
    print(f"Max Retries: {MAX_RETRIES}")
    print(f"Slack notifications: {'enabled' if SLACK_BOT_TOKEN else 'disabled (no SLACK_BOT_TOKEN)'}")

    app.run(host='0.0.0.0', port=port, debug=debug)


if __name__ == "__main__":
    main()
