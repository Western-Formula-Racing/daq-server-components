# Startup data loader

This container pre-loads InfluxDB 3 with small CAN datasets whenever the compose stack starts. It is safe to publish: the bundled data and DBC file are synthetic examples designed for development.

## Workflow

1. Waits for InfluxDB 3 to pass its health check.
2. Reads CSV files from the mounted `/data` directory (copy `2024-01-01-00-00-00.csv.md` to `2024-01-01-00-00-00.csv` for the sample dataset).
3. Uses `example.dbc` to decode each CAN frame into human-readable signals.
4. Writes the decoded metrics to InfluxDB 3 (`WFR25` bucket, `WFR` organisation) and emits line protocol for Telegraf.
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
| `INFLUXDB_TOKEN` | Token used for direct writes when `BACKFILL=1` (injected from `.env`). |
| `INFLUXDB_URL` | URL for the InfluxDB 3 instance (defaults to `http://influxdb3:8181`). |
| `BACKFILL` | Set to `1` to stream directly into InfluxDB; set to `0` to only generate line protocol for Telegraf. |

## Adding real data

1. Drop additional CSV files into `data/` using the naming convention `YYYY-MM-DD-HH-MM-SS.csv`.
2. Replace `example.dbc` with your production CAN database.
3. Rebuild the image (`docker compose build startup-data-loader`) and restart the stack.

## Monitoring

Check progress with:

```bash
docker compose logs -f startup-data-loader
```

The loader also tracks its state in `load_data_progress.json` inside the container so that it can resume large imports after interruptions.
