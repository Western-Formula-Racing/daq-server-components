import os
import shlex
import subprocess
from pathlib import Path
from threading import Event

import requests
from slack_sdk.web import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

processed_messages = set()

# --- Slack App Configuration ---
app_token = os.environ["SLACK_APP_TOKEN"]
bot_token = os.environ["SLACK_BOT_TOKEN"]

web_client = WebClient(token=bot_token)
socket_client = SocketModeClient(app_token=app_token, web_client=web_client)

WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
DEFAULT_CHANNEL = os.environ.get("SLACK_DEFAULT_CHANNEL", "C08NTG6CXL5")
AGENT_PAYLOAD_PATH = Path(os.environ.get("AGENT_PAYLOAD_PATH", "agent_payload.txt"))
AGENT_TRIGGER_COMMAND = os.environ.get("AGENT_TRIGGER_COMMAND")
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
    parts = command_full.split(maxsplit=1)
    instructions = parts[1].strip() if len(parts) > 1 else ""
    if not instructions:
        send_slack_message(
            DEFAULT_CHANNEL,
            text=f"⚠️ <@{user}> Please provide instructions after `!agent`.",
        )
        return

    try:
        AGENT_PAYLOAD_PATH.parent.mkdir(parents=True, exist_ok=True)
        AGENT_PAYLOAD_PATH.write_text(instructions + "\n", encoding="utf-8")
    except OSError as exc:
        print("Error writing agent payload:", exc)
        send_slack_message(
            DEFAULT_CHANNEL,
            text=f"❌ <@{user}> Unable to write agent payload. Error: {exc}",
        )
        return
    #TODO: Add AI + Terrarium integration here

    try:
        result = subprocess.run(
            AGENT_COMMAND,
            capture_output=True,
            text=True,
            check=True,
        )
        output = (result.stdout or "Command executed with no output").strip()
        send_slack_message(
            DEFAULT_CHANNEL,
            text=(
                f"✅ <@{user}> Agent instructions saved to `{AGENT_PAYLOAD_PATH}`."
                " Placeholder trigger output:\n```${output[:2000]}```"
            ),
        )
    except subprocess.CalledProcessError as exc:
        error_text = (exc.stderr or str(exc)).strip()
        print("Agent trigger command failed:", error_text)
        send_slack_message(
            DEFAULT_CHANNEL,
            text=(
                f"❌ <@{user}> Agent trigger command failed with exit code {exc.returncode}."
                f" Details:\n```${error_text[:2000]}```"
            ),
        )


def handle_help(user):
    help_text = (
        f"📘 <@{user}> Available Commands:\n"
        "```\n"
        "!help                      - Show this help message.\n"
        "!location                  - Show the current :daqcar: location.\n"
        "!testimage                 - Upload the bundled Lappy test image.\n"
        "!agent <instructions>      - Save instructions to the agent text file and trigger\n"
        "                              the placeholder command.\n"
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
    socket_client.socket_mode_request_listeners.append(process_events)
    try:
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
    except Exception as exc:
        print(f"🔴 Bot failed to connect: {exc}")
        import traceback

        traceback.print_exc()
