# Western Formula Racing DAQ Components

This repository hosts the Docker-based telemetry stack that powers Western Formula Racing’s data acquisition (DAQ) pipeline. It is designed to be publicly shareable: all runtime credentials live in `.env` files, sample datasets are anonymised, and every container is documented for easy onboarding.

## Repository layout

| Path | Description |
| --- | --- |
| `installer/` | Docker Compose deployment, container sources, and environment templates. |
| `docs/` | Public-facing documentation for each service and the compose stack. |
| `learn-sql/` | Jupyter notebooks for learning SQL concepts with the team’s datasets. |
| `docker-error-logger-setup.md` | Notes on configuring Docker’s logging for troubleshooting. |
| `daq-qrh-checklist.tsx` | Internal checklist component used in the frontend. |

## Quick start

1. Install Docker Desktop (macOS/Windows) or Docker Engine + Compose V2 (Linux).
2. Navigate to the installer and copy the environment template:
   ```bash
   cd installer
   cp .env.example .env
   # Update values before deploying outside of local development
   ```
3. Launch the stack:
   ```bash
   docker compose up -d
   ```
4. Visit the services:
   - InfluxDB 3 Explorer – http://localhost:8888
   - Grafana – http://localhost:8087
   - File uploader – http://localhost:8084
   - Static frontend – http://localhost:8060

All services share a bridge network named `datalink` and rely on the admin token supplied through `.env`.

## System overview

The compose stack deploys nine cooperating containers:

1. **InfluxDB 3** – Time-series database seeded with a tiny example dataset.
2. **InfluxDB 3 Explorer** – Web UI for browsing and querying telemetry.
3. **Telegraf** – Reads the importer’s line protocol output and forwards metrics to InfluxDB.
4. **Grafana** – Pre-provisioned dashboards that visualise the stored telemetry.
5. **Static frontend** – Lightweight landing page for the driver station.
6. **Slack bot** – Optional automation/notification bot for race ops.
7. **Lap analysis app (“lappy”)** – Dash-based exploration tool.
8. **Startup data loader** – Seeds the database on boot with sample CAN frames.
9. **File uploader** – Streams uploaded CSV logs into InfluxDB using the shared DBC file.

Detailed documentation for each service is available in `docs/containers/`.

## Sample data & DBC files

The repository ships with `example.dbc` (a minimal CAN database) and a markdown-wrapped sample dataset (`2024-01-01-00-00-00.csv.md`) containing four rows of synthetic telemetry. Copy the code block into a `.csv` file before running the stack. Replace both assets with production data when working with real vehicles.

## Working with environment variables

Every container reads its credentials from the `.env` file co-located with `docker-compose.yml`. Refer to `installer/.env.example` for the exhaustive list. Never commit real tokens—keep personal overrides in `.env` and add `.env` to your global gitignore.

## Documentation index

- [Compose stack reference](docs/docker-compose.md)
- [Container documentation](docs/containers/)
- [Grafana dashboards](installer/grafana/)
- [Startup data loader](installer/startup-data-loader/README.md)

## Contributing

1. Fork the repository and clone your fork.
2. Create a feature branch and commit your changes.
3. Run `docker compose up -d` inside `installer/` to verify the stack.
4. Submit a pull request with links to relevant documentation updates.

Please include documentation updates whenever you change behaviour or expose new configuration options.
