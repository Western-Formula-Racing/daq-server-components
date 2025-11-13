import os
import random
import sys
from pathlib import Path

import matplotlib.pyplot as plt

sys.path.append(str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SLACK_APP_TOKEN", "xapp-test-token")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test-token")
os.environ.setdefault("SLACK_DEFAULT_CHANNEL", "#test-channel")

from installer.slackbot.slackbot import send_slack_image, send_slack_message


class DummyWebClient:
    """A minimal stand-in for slack_sdk.web.WebClient used in tests."""

    def __init__(self) -> None:
        self.chat_calls: list[dict] = []
        self.upload_calls: list[dict] = []

    def chat_postMessage(self, **payload):
        self.chat_calls.append(payload)

    def files_upload_v2(self, *, channel, file, filename, title):
        # Read the in-memory file to ensure it was generated correctly.
        content = file.read()
        self.upload_calls.append(
            {
                "channel": channel,
                "filename": filename,
                "title": title,
                "content": content,
            }
        )


def test_send_message_and_scatter_plot(tmp_path):
    client = DummyWebClient()
    channel = "#test-channel"
    message = "Automated integration text message"

    send_slack_message(channel, message, client=client)

    assert len(client.chat_calls) == 1
    assert client.chat_calls[0]["channel"] == channel
    assert client.chat_calls[0]["text"] == message

    random.seed(0)
    x_values = [random.random() for _ in range(25)]
    y_values = [random.random() for _ in range(25)]

    fig, ax = plt.subplots()
    ax.scatter(x_values, y_values, c="tab:blue")
    ax.set_title("Random XY Scatter Plot")
    ax.set_xlabel("X Axis")
    ax.set_ylabel("Y Axis")

    image_path = tmp_path / "random_scatter.png"
    fig.savefig(image_path)
    plt.close(fig)

    assert image_path.exists()
    assert image_path.stat().st_size > 0

    send_slack_image(channel, image_path, title="Random XY Scatter Plot", client=client)

    assert len(client.upload_calls) == 1
    upload_payload = client.upload_calls[0]
    assert upload_payload["channel"] == channel
    assert upload_payload["filename"] == image_path.name
    assert upload_payload["title"] == "Random XY Scatter Plot"
    assert len(upload_payload["content"]) > 0
