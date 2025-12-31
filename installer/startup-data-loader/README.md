# Startup data loader

This container pre-loads InfluxDB 3 with small CAN datasets whenever the compose stack starts. It is safe to publish: the bundled data and DBC file are synthetic examples designed for development.

## Workflow

1. Waits for InfluxDB 3 to pass its health check.
2. Reads CSV files from the mounted `/data` directory (copy `2024-01-01-00-00-00.csv.md` to `2024-01-01-00-00-00.csv` for the sample dataset).
3. Uses the shared `/installer/example.dbc` file (or the path specified by `DBC_FILE_PATH`) to decode each CAN frame into human-readable signals.
4. Writes the decoded metrics directly to InfluxDB 3 (`WFR25` bucket, `WFR` organisation).
5. Exits once all files finish processing.

## CSV format

```
relative_ms,protocol,can_id,byte0,byte1,byte2,byte3,byte4,byte5,byte6,byte7
0,CAN,256,32,3,64,80,0,0,0,0
50,CAN,512,200,1,50,0,100,70,0,0
```

## Environment variables

| Variable | Description |
| --- | --- |
| `INFLUXDB_TOKEN` | Token used for direct writes (injected from `.env`). |
| `INFLUXDB_URL` | URL for the InfluxDB 3 instance (defaults to `http://influxdb3:8181`). |
| `CSV_RESTART_INTERVAL` | Number of CSV files to process before the loader re-execs itself (defaults to `10`; set to `0` to disable). |

## Adding real data

1. Drop additional CSV files into `data/` using the naming convention `YYYY-MM-DD-HH-MM-SS.csv`.
2. Replace the repository-level `example.dbc` (or set `DBC_FILE_PATH`) with your production CAN database.
3. Restart the stack so the new DBC is picked up by the container.

## Monitoring

Check progress with:

```bash
docker compose logs -f startup-data-loader
```

The loader also tracks its state in `load_data_progress.json` inside the container so that it can resume large imports after interruptions.