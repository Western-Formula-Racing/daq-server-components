# Health Monitor

Python service that periodically collects Docker container and application metrics and writes them to an InfluxDB 3 database.

## What it does

- **Every 60 seconds** (configurable):
  - **InfluxDB container** (`influxdb3`): Up/Down, restart count, disk usage of the data volume, write latency (and write errors if any).
  - **Scanner container** (`data-downloader-scanner`): Up/Down, and application metrics from the data-downloader API: `events_processed_per_minute`, `last_successful_job_timestamp`, `error_count`.

- Writes all metrics to InfluxDB 3 as points in the **`container_health`** measurement (tag: `container`), in the database configured by `INFLUXDB_HEALTH_DATABASE` (default: `health`).

## Requirements

- Docker socket access so the monitor can inspect containers and volume usage.
- Network access to `influxdb3` and `data-downloader-api` (same `datalink` network in docker-compose).
- InfluxDB 3 database: the target database (e.g. `health`) may need to be created in InfluxDB 3 before the first write, depending on your InfluxDB 3 setup.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HEALTH_MONITOR_INTERVAL_SECONDS` | `60` | Seconds between collection cycles. |
| `INFLUXDB_URL` | `http://influxdb3:8181` | InfluxDB 3 URL. |
| `INFLUXDB_ADMIN_TOKEN` | (from env) | Token for writing to InfluxDB 3. |
| `INFLUXDB_HEALTH_DATABASE` | `health` | Database (bucket) name for health metrics. |
| `HEALTH_MONITOR_INFLUXDB_CONTAINER` | `influxdb3` | Container name for InfluxDB. |
| `HEALTH_MONITOR_SCANNER_CONTAINER` | `data-downloader-scanner` | Container name for the scanner. |
| `HEALTH_MONITOR_SCANNER_API_URL` | `http://data-downloader-api:8000` | Base URL of the data-downloader API (for scanner metrics). |
| `HEALTH_MONITOR_INFLUXDB_VOLUME_SUFFIX` | `influxdb3-data` | Volume name suffix used to find InfluxDB data volume for disk usage. |

## Running

The service is defined in the main installer `docker-compose.yml` as `health-monitor`. Start the stack (including `influxdb3` and `data-downloader-api` / `data-downloader-scanner`) and the monitor will run automatically.

```bash
docker compose up -d
# or
docker compose up -d influxdb3 data-downloader-api data-downloader-scanner health-monitor
```

Logs:

```bash
docker compose logs -f health-monitor
```
