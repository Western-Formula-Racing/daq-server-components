# DAQ Telemetry MCP Server

This is a standalone, spec-style MCP server (Python SDK) for DAQ telemetry tooling.

## Tools

- `list_seasons`
- `resolve_season`
- `get_runs`
- `list_sensors`
- `query_signal`
- `validate_request`

All tools are read-only and call the existing `data-downloader` API.

## Run (stdio)

```bash
cd /home/ubuntu/projects/daq-server-components/installer/mcp-server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export DATA_DOWNLOADER_URL=http://localhost:8000
python server.py
```

Important: this is a stdio MCP server. Do not print to stdout in server code.

## VS Code / MCP host config example

Use your MCP host configuration to launch:

- command: `python`
- args: `[/home/ubuntu/projects/daq-server-components/installer/mcp-server/server.py]`
- env: `DATA_DOWNLOADER_URL=http://localhost:8000`

## Local sanity checks

Before starting the MCP server, verify upstream API is healthy:

```bash
curl -s http://localhost:8000/api/health
curl -s http://localhost:8000/api/seasons
```

## Next migration step

Current `code-generator` still uses an internal MCP-style bridge. To fully migrate,
switch it to an MCP client and call this server over MCP transport.
