# DAQ Installer

This directory contains the Docker Compose deployment used to run the full telemetry pipeline for the Western Formula Racing data acquisition (DAQ) system. It is safe to publish publicly—sensitive credentials are injected at runtime from a local `.env` file and the sample datasets are intentionally anonymised.

## Contents

- `docker-compose.yml` – Orchestrates all runtime containers.
- `.env.example` – Template for environment variables required by the stack.
- `influxdb3-admin-token.json` – Development token consumed by the InfluxDB 3 server on first start.
- `influxdb3-explorer-config/` – Configuration for the optional InfluxDB web explorer container.
- Service folders (for example `file-uploader/`, `startup-data-loader/`, `slackbot/`) – Each contains the Docker context and service-specific source code.

## Prerequisites

- Docker Desktop 4.0+ or Docker Engine 24+
- Docker Compose V2 (bundled with recent Docker releases)

## Quick start

1. Copy the environment template and adjust the values for your environment:
   ```bash
   cd installer
   cp .env.example .env
   # Update tokens/passwords before deploying to production
   ```
2. Launch the stack:
   ```bash
   docker compose up -d
   ```
3. Verify the services:
   ```bash
   docker compose ps
   docker compose logs influxdb3 | tail
   ```
4. Tear the stack down when you are finished:
   ```bash
   docker compose down -v
   ```

The first boot seeds InfluxDB 3 with the sample CAN data in `startup-data-loader/data/`. Subsequent restarts skip the import unless you remove the volumes.

## Environment variables

All secrets and tokens are defined in `.env`. The defaults provided in `.env.example` are development-safe placeholders and **must** be replaced for production deployments.

| Variable | Purpose | Default |
| --- | --- | --- |
| `DBC_FILE_PATH` | Path to the CAN DBC file used by startup-data-loader and file-uploader and other services | `example.dbc` |
| `INFLUXDB_URL` | Internal URL used by services to talk to InfluxDB 3 | `http://influxdb3:8181` |
| `INFLUXDB_INIT_USERNAME` / `INFLUXDB_INIT_PASSWORD` | Bootstraps the initial admin user | `admin` / `dev-influxdb-password` |
| `INFLUXDB_ADMIN_TOKEN` | API token shared by all services | `dev-influxdb-admin-token` |
| `GRAFANA_ADMIN_PASSWORD` | Grafana administrator password | `dev-grafana-password` |
| `EXPLORER_SESSION_SECRET` | Secret for the InfluxDB 3 Explorer UI | `dev-explorer-session-key` |
| `ENABLE_SLACK` | Gate to disable Slack-specific services | `false` |
| `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` | Credentials for the Slack bot (optional) | empty |
| `SLACK_WEBHOOK_URL` | Incoming webhook for notifications (optional) | empty |
| `SLACK_DEFAULT_CHANNEL` | Default Slack channel ID for outbound messages | `C0123456789` |
| `FILE_UPLOADER_WEBHOOK_URL` | Webhook invoked after uploads complete | inherits `SLACK_WEBHOOK_URL` |
| `DEBUG` | Enables verbose logging for selected services | `0` |

> **Security reminder:** Replace every default value when deploying outside of a local development environment. Generate secure tokens with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.

## Service catalogue

| Service | Ports | Description |
| --- | --- | --- |
| `influxdb3` | `9000` (mapped to `8181` internally) | Core time-series database. Initialised with the admin token from `.env`. |
| `influxdb3-explorer` | `8888` | Lightweight UI for browsing data in InfluxDB 3. |
| `data-downloader` | `3000` | Periodically downloads CAN CSV archives from the DAQ server. Visual SQL query builder included. |
| `telegraf` | n/a | Collects CAN metrics produced by the importer and forwards them to InfluxDB. |
| `grafana` | `8087` | Visualises telemetry with pre-provisioned dashboards. |
| `slackbot` | n/a | Socket-mode Slack bot for notifications and automation (optional). |
| `lap-detector` | `8050` | Dash-based lap analysis web application. |
| `startup-data-loader` | n/a | Seeds InfluxDB with sample CAN frames on first boot. |
| `file-uploader` | `8084` | Web UI for uploading CAN CSV archives and streaming them into InfluxDB. |

## Data and DBC files

- `startup-data-loader/data/` ships with `2025-01-01-00-00-00.csv`, a csv file to exercise the import pipeline without exposing production telemetry.
- Both the loader and the uploader share `example.dbc`, a minimal CAN database that defines two demo messages. Replace this file with your team’s CAN definition when working with real data.

## Observability

- Grafana dashboards are provisioned automatically from `grafana/dashboards/` and use the datasource in `grafana/provisioning/datasources/`.
- Telegraf writes processed metrics to `/var/lib/telegraf/can_metrics.out` before forwarding them to InfluxDB. Inspect this file inside the container for debugging (`docker compose exec telegraf tail -f /var/lib/telegraf/can_metrics.out`).

## Troubleshooting tips

- **Service fails to connect to InfluxDB** – Confirm the token in `.env` matches `influxdb3-admin-token.json`. Regenerate the volumes with `docker compose down -v` if you rotate credentials.
- **Re-import sample data** – Remove the `telegraf-data` volume and rerun the stack.
- **Slack services are optional** – Leave Slack variables empty or set `ENABLE_SLACK=false` to skip starting the bot during development.

## Next steps

- Replace the example dataset and `example.dbc` file with production equivalents once you are ready to ingest real telemetry.
- Update the Grafana dashboards under `grafana/dashboards/` to match your data model.
- Review each service’s README in its respective directory for implementation details.
