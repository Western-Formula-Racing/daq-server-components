"""
Code Generation Service - Orchestrator for Cohere + Sandbox execution.
Receives requests from Slackbot, generates code using Cohere, and executes in sandbox.

Supports two modes:
  1. Single-shot  (POST /api/generate-code) — one prompt → one code block → retry on error
  2. Multi-step   (POST /api/agent)         — agentic loop: reason → code → observe → repeat
"""

from __future__ import annotations

import json
import os
import re
import time
import base64
from pathlib import Path
from typing import Dict, Any, Generator, List, Optional

from dotenv import load_dotenv
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import cohere
import requests

# Load environment variables
load_dotenv()

# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------
COHERE_API_KEY = os.getenv("COHERE_API_KEY")
if not COHERE_API_KEY:
    raise RuntimeError(
        "COHERE_API_KEY not found in environment. Add it to your .env or export it as an env var."
    )

COHERE_MODEL = os.getenv("COHERE_MODEL", "command-a-reasoning-08-2025")
SANDBOX_URL = os.getenv("SANDBOX_URL", "http://sandbox-runner:9090")
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "6"))

# Configure Cohere client
co = cohere.Client(COHERE_API_KEY)

# Paths
BASE_DIR = Path(__file__).resolve().parent
PROMPT_GUIDE_PATH = BASE_DIR / "prompt-guide.txt"
AGENT_PROMPT_PATH = BASE_DIR / "agent-prompt-guide.txt"
GENERATED_CODE_PATH = BASE_DIR / "generated_sandbox_code.py"

# ---------------------------------------------------------------------
# Flask App Setup
# ---------------------------------------------------------------------
app = Flask(__name__)
CORS(app)

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


def load_agent_prompt() -> str:
    """Reads the agent-specific prompt guide file (multi-step mode)."""
    if AGENT_PROMPT_PATH.exists():
        return AGENT_PROMPT_PATH.read_text().strip()
    # Fall back to standard guide
    return load_prompt_guide()


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


def request_python_code(guide: str, prompt: str) -> str:
    """Request Python code from Cohere."""
    # Combine guide and user prompt
    full_prompt = f"{guide}\n\n{prompt}"

    response = co.chat(
        message=full_prompt,
        model=COHERE_MODEL,
        temperature=0.2,
    )

    # Extract Python code from response
    raw_output = response.text
    python_code = extract_python_code(raw_output)

    # Save generated code
    GENERATED_CODE_PATH.write_text(python_code, encoding="utf-8")
    print(f"Generated code written to {GENERATED_CODE_PATH}")

    return python_code


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
    # Process output files from custom sandbox
    files_info = []
    for file_data in sandbox_result.get("output_files", []):
        file_info = {
            "name": file_data.get("filename"),
            "data": file_data.get("b64_data"),
            "type": "image" if file_data.get("filename", "").endswith((".png", ".jpg", ".jpeg", ".gif", ".svg")) else "file"
        }
        files_info.append(file_info)
    
    result = {
        "status": "success" if sandbox_result.get("ok") else "error",
        "output": sandbox_result.get("std_out", "").strip(),
        "error": sandbox_result.get("std_err", "").strip(),
        "return_code": sandbox_result.get("return_code"),
        "files": files_info
    }
    return result


# ---------------------------------------------------------------------
# Multi-step Agent Logic
# ---------------------------------------------------------------------

# Regex that finds all ```python ... ``` fenced code blocks
_CODE_FENCE_RE = re.compile(
    r"```python\s*\n(.*?)```", re.DOTALL | re.IGNORECASE
)
# Detect the "FINAL_ANSWER:" marker from the LLM
_FINAL_ANSWER_RE = re.compile(
    r"FINAL_ANSWER:\s*(.*)", re.DOTALL | re.IGNORECASE
)


def _extract_code_blocks(text: str) -> List[str]:
    """Return all ```python ... ``` fenced code blocks from *text*."""
    blocks = _CODE_FENCE_RE.findall(text)
    if blocks:
        return [b.strip() for b in blocks if b.strip()]
    # Fallback: if the entire response looks like raw Python (no markdown)
    stripped = text.strip()
    if stripped and "FINAL_ANSWER:" not in stripped and _looks_like_python(stripped):
        return [stripped]
    return []


def _looks_like_python(text: str) -> bool:
    """Quick heuristic: does the text start with typical Python tokens?"""
    first_line = text.lstrip().split("\n", 1)[0].strip()
    return any(
        first_line.startswith(kw)
        for kw in ("import ", "from ", "def ", "class ", "#", "print(")
    )


def _extract_final_answer(text: str) -> Optional[str]:
    """If the LLM wrote FINAL_ANSWER: ..., return the answer text."""
    m = _FINAL_ANSWER_RE.search(text)
    if m:
        return m.group(1).strip()
    return None


def _format_observation(sandbox_result: Dict[str, Any]) -> str:
    """
    Turn a sandbox result dict into a human-readable observation string
    that will be fed back to the LLM as context for the next step.
    """
    parts: list[str] = []

    if sandbox_result.get("ok"):
        parts.append("✅ Code executed successfully.")
    else:
        rc = sandbox_result.get("return_code", "?")
        parts.append(f"❌ Code failed (return code {rc}).")

    stdout = (sandbox_result.get("std_out") or "").strip()
    stderr = (sandbox_result.get("std_err") or "").strip()

    if stdout:
        # Truncate very long output so the context window isn't blown
        if len(stdout) > 8000:
            stdout = stdout[:4000] + "\n... (truncated) ...\n" + stdout[-2000:]
        parts.append(f"STDOUT:\n{stdout}")

    if stderr:
        if len(stderr) > 4000:
            stderr = stderr[:2000] + "\n... (truncated) ...\n" + stderr[-1000:]
        parts.append(f"STDERR:\n{stderr}")

    files = sandbox_result.get("output_files", [])
    if files:
        names = [f.get("filename", "?") for f in files]
        parts.append(f"Generated files: {', '.join(names)}")

    return "\n".join(parts)


def stream_agent_loop(
    user_prompt: str,
    guide: str,
    max_steps: int = MAX_AGENT_STEPS,
) -> Generator[Dict[str, Any], None, None]:
    """
    Agentic multi-step loop that *yields* one event dict per step.

    Event types:
      - {"event": "thinking",  "step": N, "thought": "...", "max_steps": M}
      - {"event": "executed",  "step": N, "thought": "...", "code": "...", "result": {...}, "files": [...]}
      - {"event": "final",     "step": N, "final_answer": "...", "files": [...]}
      - {"event": "error",     "error": "..."}
      - {"event": "max_steps", "step": N, "total_steps": N}
    """
    chat_history: List[Dict[str, str]] = []
    all_files: List[Dict[str, Any]] = []

    for step_num in range(1, max_steps + 1):
        print(f"\n{'='*60}")
        print(f"Agent step {step_num}/{max_steps}")
        print(f"{'='*60}\n")

        # Build the current message
        if step_num == 1:
            current_message = user_prompt
        else:
            current_message = (
                "Based on the execution results above, continue working toward "
                "the original goal. If the task is complete, respond with "
                "FINAL_ANSWER: followed by your summary. Otherwise, write the "
                "next Python code block to execute."
            )

        # --- Emit "thinking" event so the user knows the LLM is working ---
        yield {
            "event": "thinking",
            "step": step_num,
            "max_steps": max_steps,
            "thought": f"Thinking about step {step_num}...",
        }

        # Call Cohere with conversation history
        response = co.chat(
            message=current_message,
            preamble=guide,
            chat_history=chat_history,
            model=COHERE_MODEL,
            temperature=0.2,
        )
        llm_text = response.text

        print(f"LLM response (step {step_num}):\n{llm_text[:500]}...")

        # Record the turn in history
        chat_history.append({"role": "USER", "message": current_message})
        chat_history.append({"role": "CHATBOT", "message": llm_text})

        # --- Check for FINAL_ANSWER ---
        final_answer = _extract_final_answer(llm_text)
        if final_answer:
            print(f"✅ Agent produced FINAL_ANSWER at step {step_num}")
            yield {
                "event": "final",
                "step": step_num,
                "final_answer": final_answer,
                "files": all_files,
            }
            return

        # --- Extract and execute code ---
        code_blocks = _extract_code_blocks(llm_text)

        if not code_blocks:
            # No code and no FINAL_ANSWER → treat response as the final answer
            yield {
                "event": "final",
                "step": step_num,
                "final_answer": llm_text.strip(),
                "files": all_files,
            }
            return

        combined_code = "\n\n".join(code_blocks)
        GENERATED_CODE_PATH.write_text(combined_code, encoding="utf-8")

        # Execute in sandbox
        sandbox_result = submit_code_to_sandbox(combined_code)
        formatted = format_sandbox_result(sandbox_result)

        # Collect output files
        step_files = formatted.get("files", [])
        all_files.extend(step_files)

        # --- Emit "executed" event with step results ---
        thought_text = llm_text.split("```")[0].strip()
        yield {
            "event": "executed",
            "step": step_num,
            "thought": thought_text,
            "code": combined_code,
            "result": formatted,
            "files": step_files,
        }

        # Build observation for the next turn
        observation = _format_observation(sandbox_result)
        chat_history.append({
            "role": "USER",
            "message": f"[Execution result from step {step_num}]\n{observation}",
        })

    # Exhausted all steps without a FINAL_ANSWER
    print(f"⚠️  Agent reached max steps ({max_steps}) without FINAL_ANSWER")
    yield {
        "event": "max_steps",
        "step": max_steps,
        "total_steps": max_steps,
        "files": all_files,
    }


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "code-generator"})


@app.route('/api/generate-code', methods=['POST'])
def generate_code():
    """Generate and execute Python code based on user prompt with automatic retries on failure."""
    try:
        data = request.get_json()
        user_prompt = data.get('prompt', '').strip()
        
        if not user_prompt:
            return jsonify({"error": "Prompt is required"}), 400

        # Load the prompt guide
        guide = load_prompt_guide()
        
        retry_info = []
        current_prompt = user_prompt
        python_code = None
        
        # Try up to MAX_RETRIES + 1 times (initial attempt + retries)
        for attempt in range(MAX_RETRIES + 1):
            print(f"\n{'='*60}")
            print(f"Attempt {attempt + 1}/{MAX_RETRIES + 1}")
            print(f"{'='*60}\n")
            
            # Generate Python code using Cohere
            python_code = request_python_code(guide, current_prompt)

            # Execute the code in sandbox
            sandbox_result = submit_code_to_sandbox(python_code)
            
            # Check if execution was successful
            if sandbox_result.get("ok"):
                # Success! Format and return result
                result = format_sandbox_result(sandbox_result)
                
                response = {
                    "code": python_code,
                    "result": result
                }
                
                # Include retry information if any retries were made
                if retry_info:
                    response["retries"] = retry_info
                    print(f"✅ Success after {len(retry_info)} retry/retries")
                
                return jsonify(response)
            
            # Execution failed
            if attempt < MAX_RETRIES:
                # We have retries left
                error_message = format_error_for_retry(sandbox_result)
                retry_info.append({
                    "attempt": attempt + 1,
                    "error": error_message
                })
                
                print(f"\n{'='*60}")
                print(f"RETRY {attempt + 1}/{MAX_RETRIES} - Code execution failed")
                print(f"{'='*60}")
                print(error_message)
                print(f"\n{'='*60}")
                print("Retrying with error feedback...")
                print(f"{'='*60}\n")
                
                # Append error to prompt for retry
                current_prompt = f"""{user_prompt}

The previous code generated had the following error:

{error_message}

Please fix the code to address this error."""
            else:
                # No more retries left, return the error
                print(f"\n{'='*60}")
                print(f"❌ All {MAX_RETRIES} retries exhausted - returning error")
                print(f"{'='*60}\n")
                result = format_sandbox_result(sandbox_result)
                
                return jsonify({
                    "code": python_code,
                    "result": result,
                    "retries": retry_info,
                    "max_retries_reached": True
                })

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


@app.route('/api/agent', methods=['POST'])
def agent():
    """
    Multi-step agent endpoint — streams results via Server-Sent Events (SSE).

    Accepts:
        { "prompt": "...", "max_steps": 6 }   (max_steps is optional)

    Streams newline-delimited SSE events, one JSON object per line:
        data: {"event": "thinking",  "step": 1, ...}
        data: {"event": "executed",  "step": 1, "thought": "...", "code": "...", "result": {...}}
        data: {"event": "final",     "step": 3, "final_answer": "...", "files": [...]}
    """
    try:
        data = request.get_json()
        user_prompt = data.get('prompt', '').strip()

        if not user_prompt:
            return jsonify({"error": "Prompt is required"}), 400

        max_steps = int(data.get('max_steps', MAX_AGENT_STEPS))
        max_steps = min(max_steps, MAX_AGENT_STEPS)  # enforce server cap

        guide = load_agent_prompt()

        def generate_sse():
            try:
                for step_event in stream_agent_loop(user_prompt, guide, max_steps=max_steps):
                    yield f"data: {json.dumps(step_event)}\n\n"
            except Exception as exc:
                import traceback
                traceback.print_exc()
                yield f"data: {json.dumps({'event': 'error', 'error': str(exc)})}\n\n"

        return Response(
            generate_sse(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # disable nginx buffering
            },
        )

    except Exception as e:
        print(f"Error in agent: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "error": str(e),
            "steps": [],
            "final_answer": None,
            "total_steps": 0,
            "files": []
        }), 500


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main():
    """Start the code generation service."""
    port = int(os.getenv("CODE_GEN_PORT", "3030"))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    
    print(f"Starting code generation service on http://0.0.0.0:{port}")
    print(f"Cohere Model: {COHERE_MODEL}")
    print(f"Sandbox URL: {SANDBOX_URL}")
    print(f"Max Retries: {MAX_RETRIES}")
    
    app.run(host='0.0.0.0', port=port, debug=debug)


if __name__ == "__main__":
    main()
