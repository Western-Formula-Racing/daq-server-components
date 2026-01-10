# Slack Bot Agent Command Documentation

## Overview

The `slack_bot.py` script runs a Socket Mode client that listens for commands in a specific Slack channel. The core integration point for external agent interaction is the `!agent` command, managed by the `handle_agent` function.

## The `!agent` Command Flow

When a user types `!agent <instructions>` (e.g., `!agent analyze the logs`), the bot performs the following steps:

1.  **Input Parsing:** 
    - It strips the `!agent` prefix to isolate the `instructions` string.
    - If no instructions are provided, it returns a warning message.

2.  **Local Persistence:** 
    - The instructions are written to a local file defined by `AGENT_PAYLOAD_PATH` (default: `agent_payload.txt`).

3.  **HTTP POST Request:** 
    - The bot sends a POST request to the `SANDBOX_URL` (default: `http://sandbox:8080`).
    - It sets a timeout of 30 seconds.

4.  **Response Handling:** 
    - If the request is successful, the bot captures the response.
    - It attempts to parse the response as JSON; if that fails, it falls back to raw text.
    - The first 2000 characters of the result are sent back to the Slack channel.

## HTTP Request Structure

### Request Payload

The bot sends the user's instructions as a JSON object with a single key: `prompt`.

**Example Request:**

```json
{
  "prompt": "Please analyze the latest server logs and summarize errors."
}
```

### Response Expectation

While the bot handles raw text responses, a JSON response is preferred for structured data handling.

**Example Response:**

```json
{
  "status": "success",
  "job_id": "12345",
  "message": "Analysis started. ETA 2 minutes."
}
```
