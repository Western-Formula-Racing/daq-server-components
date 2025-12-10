import os
import shlex
import subprocess
import base64
import json
import datetime
from pathlib import Path
from threading import Event

import requests
from slack_sdk.web import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

processed_messages = set()

# --- Logging Configuration ---
LOG_DIR = Path("/app/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

# --- Slack App Configuration ---
app_token = os.environ["SLACK_APP_TOKEN"]
bot_token = os.environ["SLACK_BOT_TOKEN"]

print(f"DEBUG: Loaded SLACK_APP_TOKEN: {app_token[:9]}...{app_token[-4:]} (Length: {len(app_token)})")
print(f"DEBUG: Loaded SLACK_BOT_TOKEN: {bot_token[:9]}...{bot_token[-4:]} (Length: {len(bot_token)})")

web_client = WebClient(token=bot_token)
socket_client = SocketModeClient(
    app_token=app_token,
    web_client=web_client,
    trace_enabled=True,  # Enable debug logging
    ping_interval=30,    # Send ping every 30 seconds
    auto_reconnect_enabled=True
)

WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
DEFAULT_CHANNEL = os.environ.get("SLACK_DEFAULT_CHANNEL", "C08NTG6CXL5")
AGENT_PAYLOAD_PATH = Path(os.environ.get("AGENT_PAYLOAD_PATH", "agent_payload.txt"))
AGENT_TRIGGER_COMMAND = os.environ.get("AGENT_TRIGGER_COMMAND")
CODE_GENERATOR_URL = os.environ.get("CODE_GENERATOR_URL", "http://code-generator:3030")
DEFAULT_AGENT_COMMAND = [
    "python3",
    "-c",
    "print('Agent trigger placeholder executed')",
]
AGENT_COMMAND = shlex.split(AGENT_TRIGGER_COMMAND) if AGENT_TRIGGER_COMMAND else DEFAULT_AGENT_COMMAND


# --- Public helper functions ---
def send_slack_message(channel: str, text: str, **kwargs):
    """Send a text message to a Slack channel."""
    return web_client.chat_postMessage(channel=channel, text=text, **kwargs)


def send_slack_image(channel: str, file_path: str, **kwargs):
    """Upload an image/file to a Slack channel."""
    upload_kwargs = {
        "channel": channel,
        "file": file_path,
        "filename": os.path.basename(file_path),
    }
    upload_kwargs.update(kwargs)
    return web_client.files_upload_v2(**upload_kwargs)

def log_interaction(user, instructions, result, status, error=None):
    """Log the entire interaction to a file."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_entry_dir = LOG_DIR / f"{timestamp}_{user}"
    log_entry_dir.mkdir(parents=True, exist_ok=True)

    log_data = {
        "timestamp": timestamp,
        "user": user,
        "instructions": instructions,
        "status": status,
        "error": error,
        "generated_code": result.get("generated_code", ""),
        "output": result.get("result", {}).get("output", "")
    }

    # Save textual log
    with open(log_entry_dir / "interaction.json", "w") as f:
        json.dump(log_data, f, indent=4)

    # Save generated files (images)
    exec_result = result.get("result", {})
    files = exec_result.get("files", [])
    for file_info in files:
        filename = file_info.get("name")
        b64_data = file_info.get("data")
        file_type = file_info.get("type")
        
        if file_type == "image" and b64_data:
            try:
                image_data = base64.b64decode(b64_data)
                (log_entry_dir / filename).write_bytes(image_data)
            except Exception as e:
                print(f"Error saving log image {filename}: {e}")

    print(f"📝 Logged interaction to {log_entry_dir}")


# --- Slack Command Handlers ---
# Not currently used: handle_location
def handle_location(user):
    try:
        response = requests.get("http://lap-detector-server:8050/api/track?type=location", timeout=5)
        response.raise_for_status()
        loc = response.json().get("location", {})
        lat, lon = loc.get("lat"), loc.get("lon")
        if lat is None or lon is None:
            raise ValueError("Location payload missing lat/lon")
        map_url = f"https://www.google.com/maps/@{lat},{lon},17z"
        send_slack_message(
            DEFAULT_CHANNEL,
            text=(
                f"📍 <@{user}> Current :daqcar: location:\n"
                f"<{map_url}|View on Map>\nLatitude: {lat}\nLongitude: {lon}"
            ),
        )
    except Exception as exc:
        print("Error fetching location:", exc)
        send_slack_message(
            DEFAULT_CHANNEL,
            text=f"❌ <@{user}> Failed to retrieve car location. Error: {exc}",
        )


def handle_testimage(user):
    try:
        send_slack_image(
            DEFAULT_CHANNEL,
            file_path="lappy_test_image.png",
            title="Lappy Test Image",
            initial_comment=f"🖼️ <@{user}> Here's the test image:",
        )
    except Exception as exc:
        print("Error uploading image:", exc)
        send_slack_message(
            DEFAULT_CHANNEL,
            text=f"❌ <@{user}> Failed to upload image. Error: {exc}",
        )


def handle_agent(user, command_full):
    """
    Handle !agent command - sends request to code-generator service.
    Supports AI-powered code generation and execution.
    """
    parts = command_full.split(maxsplit=1)
    instructions = parts[1].strip() if len(parts) > 1 else ""
    if not instructions:
        send_slack_message(
            DEFAULT_CHANNEL,
            text=f"⚠️ <@{user}> Please provide instructions after `!agent`.",
        )
        return

    # Send initial acknowledgment
    send_slack_message(
        DEFAULT_CHANNEL,
        text=f"🤖 <@{user}> Processing your request: `{instructions[:100]}...`\nGenerating code with AI...",
    )

    try:
        # Call code-generator service
        response = requests.post(
            f"{CODE_GENERATOR_URL}/api/generate-code",
            json={"prompt": instructions},
            timeout=120  # Allow up to 2 minutes for code generation + retries
        )
        response.raise_for_status()
        result = response.json()
        
        # Check if retries occurred
        retries = result.get("retries", [])
        if retries:
            retry_msg = f"⚠️ Initial code had errors. Retried {len(retries)} time(s) with error feedback."
            send_slack_message(DEFAULT_CHANNEL, text=retry_msg)
        
        # Get execution result
        exec_result = result.get("result", {})
        status = exec_result.get("status", "unknown")
        
        if status == "success":
            # Success - send output and files
            output = exec_result.get("output", "")
            files = exec_result.get("files", [])
            
            success_msg = f"✅ <@{user}> Code executed successfully!"
            if retries:
                success_msg += f" (after {len(retries)} retry/retries)"
            
            send_slack_message(DEFAULT_CHANNEL, text=success_msg)
            
            # Send output if any
            if output:
                output_msg = f"**Output:**\n```\n{output[:2000]}\n```"
                send_slack_message(DEFAULT_CHANNEL, text=output_msg)
            
            # Send generated files (images, etc.)
            for file_info in files:
                filename = file_info.get("name")
                b64_data = file_info.get("data")
                file_type = file_info.get("type")
                
                if file_type == "image" and b64_data:
                    try:
                        # Decode base64 and save temporarily
                        temp_path = Path(f"/tmp/{filename}")
                        image_data = base64.b64decode(b64_data)
                        temp_path.write_bytes(image_data)
                        
                        # Upload to Slack
                        send_slack_image(
                            DEFAULT_CHANNEL,
                            str(temp_path),
                            title=f"Generated: {filename}",
                            initial_comment=f"📊 <@{user}> Here's your visualization:"
                        )
                        
                        # Clean up
                        temp_path.unlink()
                    except Exception as e:
                        print(f"Error uploading image {filename}: {e}")
                        send_slack_message(
                            DEFAULT_CHANNEL,
                            text=f"⚠️ Could not upload image {filename}: {e}"
                        )
            
            # Log successful interaction
            log_interaction(user, instructions, result, "success")
        
        else:
            # Execution failed
            error = exec_result.get("error", "Unknown error")
            max_retries_reached = result.get("max_retries_reached", False)
            
            error_msg = f"❌ <@{user}> Code execution failed"
            if max_retries_reached:
                error_msg += f" after {len(retries)} retries"
            error_msg += f":\n```\n{error[:1500]}\n```"
            
            send_slack_message(DEFAULT_CHANNEL, text=error_msg)
            
            # Log failed interaction
            log_interaction(user, instructions, result, "failed", error)
    
    except requests.exceptions.Timeout:
        send_slack_message(
            DEFAULT_CHANNEL,
            text=f"⏱️ <@{user}> Request timed out. The task might be too complex or the service is busy.",
        )
        log_interaction(user, instructions, {}, "timeout", "Request timed out")
    except requests.exceptions.RequestException as e:
        send_slack_message(
            DEFAULT_CHANNEL,
            text=f"❌ <@{user}> Failed to connect to code generation service: {e}",
        )
        log_interaction(user, instructions, {}, "connection_error", str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        send_slack_message(
            DEFAULT_CHANNEL,
            text=f"❌ <@{user}> Unexpected error: {e}",
        )
        log_interaction(user, instructions, {}, "unexpected_error", str(e))


def handle_help(user):
    help_text = (
        f"📘 <@{user}> Available Commands:\n"
        "```\n"
        "!help                      - Show this help message.\n"
        "!location                  - Show the current :daqcar: location.\n"
        "!testimage                 - Upload the bundled Lappy test image.\n"
        "!agent <instructions>      - Generate and execute Python code using AI.\n"
        "                              Example: !agent plot inverter voltage vs current\n"
        "                              Automatically retries up to 2 times if code fails.\n"
        "```"
    )
    send_slack_message(DEFAULT_CHANNEL, text=help_text)


# --- Event Processing Logic ---
def process_events(client: SocketModeClient, req: SocketModeRequest):
    if req.type != "events_api":
        return

    client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
    event = req.payload.get("event", {})
    if event.get("type") != "message" or event.get("subtype") is not None:
        return

    if event.get("channel") != DEFAULT_CHANNEL:
        return

    msg_ts = event.get("ts")
    if msg_ts in processed_messages:
        print(f"Skipping already processed message: {msg_ts}")
        return

    processed_messages.add(msg_ts)
    if len(processed_messages) > 1000:
        oldest_ts = sorted(processed_messages)[0]
        processed_messages.remove(oldest_ts)

    user = event.get("user")
    bot_user_id = os.environ.get("SLACK_BOT_USER_ID", "U08P8KS8K25")
    if user == bot_user_id:
        print(f"Skipping message from bot itself ({bot_user_id}).")
        return

    text = event.get("text", "").strip()
    if not text.startswith("!"):
        return

    command_full = text[1:]
    command_parts = command_full.split()
    main_command = command_parts[0] if command_parts else ""

    print(
        f"Received command: '{command_full}' from user {user} "
        f"in channel {event.get('channel')}"
    )

    if main_command == "location":
        handle_location(user)
    elif main_command == "testimage":
        handle_testimage(user)
    elif main_command == "agent":
        handle_agent(user, command_full)
    elif main_command == "help":
        handle_help(user)
    else:
        send_slack_message(
            DEFAULT_CHANNEL,
            text=f"❓ <@{user}> Unknown command: `{text}`. Try `!help`.",
        )


# --- Main Execution ---
if __name__ == "__main__":
    print("🟢 Bot attempting to connect...")
    print("🔍 Testing Slack API connectivity...")
    
    # Test basic connectivity first
    try:
        response = requests.get("https://slack.com/api/api.test", timeout=10)
        print(f"✓ Slack API reachable: {response.status_code}")
    except requests.exceptions.Timeout:
        print("❌ Timeout connecting to Slack API - check firewall/network")
    except Exception as e:
        print(f"❌ Cannot reach Slack API: {e}")
    
    socket_client.socket_mode_request_listeners.append(process_events)
    try:
        print("🔌 Connecting to Slack WebSocket...")
        socket_client.connect()
        if WEBHOOK_URL:
            requests.post(
                WEBHOOK_URL,
                json={"text": "Lappy on duty! :lappy:"},
                timeout=5,
            )
        else:
            print("⚠️ SLACK_WEBHOOK_URL not configured - skipping webhook notification")
        print("🟢 Bot connected and listening for messages.")
        Event().wait()
    except KeyboardInterrupt:
        print("\n🛑 Bot shutting down...")
        socket_client.close()
    except Exception as exc:
        print(f"🔴 Bot failed to connect: {exc}")
        print("💡 Possible causes:")
        print("   - Firewall blocking outbound WebSocket connections (port 443)")
        print("   - Invalid tokens (check SLACK_APP_TOKEN and SLACK_BOT_TOKEN)")
        print("   - Network connectivity issues")
        import traceback

        traceback.print_exc()
