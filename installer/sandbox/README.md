# Terrarium–Slackbot Integration

Run Python code from Slack via an Orchestrator and Cohere-Terrarium.

```
[User in Slack] → [Slackbot (Lappy)] → [Orchestrator] → [Cohere-Terrarium] → [Sandbox Container]
        ↑                                                                                  ↓
        └────────────────────────────── [Results (images, logs)]  ←────────────────────────┘

```

Used for generating telemetry plots (e.g., inverter voltage vs current) directly in Slack.

Setup

```
docker compose up -d terrarium
```

Test Script

```
python test_terrarium.py
```

