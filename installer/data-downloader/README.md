# Data Downloader Webapp

This project packages the DAQ data-downloader experience into a small stack:

- **React frontend** (`frontend/`) for browsing historic runs, triggering scans, and annotating runs.
- **FastAPI backend** (`backend/`) that reads/writes JSON state, exposes REST endpoints, and can launch scans on demand.
- **Scanner worker** (separate Docker service) that periodically runs the InfluxDB availability scan plus the unique sensor collector and exports the results to `data/runs.json` and `data/sensors.json`.

Both JSON files are shared through the `./data` directory so every service (frontend, API, scanner) sees the latest state. Notes added in the UI are stored in the same JSON payload next to the run entry.

## Getting started

1. Duplicate the sample env file and fill in the InfluxDB credentials:
   ```bash
   cp .env.example .env
   ```
2. Build + launch everything:
   ```bash
   docker compose up --build
   ```
3. Open http://localhost:3000 to access the web UI, and keep the API running on http://localhost:8000 if you want to call it directly.

## Runtime behaviour

- `frontend` serves the compiled React bundle via nginx. The UI calls the API using the `VITE_API_BASE_URL` value that gets baked into the build (defaults to http://localhost:8000). Match this host in `ALLOWED_ORIGINS` so CORS preflights succeed when the UI hits the API from another port.
- `api` runs `uvicorn backend.app:app`, exposing
  - `GET /api/runs` and `GET /api/sensors`
  - `POST /api/runs/{key}/note` to persist notes per run
  - `POST /api/scan` to fire an on-demand scan that refreshes both JSON files in the background
  - `POST /api/data/query` to request a timeseries slice for a given `signalName` between two timestamps
- `scanner` reuses the same backend image but runs `python -m backend.periodic_worker` so the scan + unique sensor collection happens at the interval defined by `SCAN_INTERVAL_SECONDS`.

Set `INFLUX_SCHEMA`/`INFLUX_TABLE` to the same values used in the legacy scripts (e.g. `iox` + `WFR25`) so the SQL sent from `server_scanner.py` and `sql.py` matches the proven queries.

All services mount `./data` inside the container and the FastAPI layer manages file I/O with atomic writes to keep data consistent between the worker and UI actions. If the rolling lookback produces no sensors, the collector automatically falls back to the historic 2025-06-19 -> 2025-07-10 window (tune via `SENSOR_FALLBACK_START` / `SENSOR_FALLBACK_END`).
