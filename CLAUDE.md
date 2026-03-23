# DAQ Server Components — Claude Notes

## Project Structure

- `installer/` — Docker Compose stack: InfluxDB3, Grafana, startup-data-loader, file-uploader, slackbot, sandbox, grafana-bridge
- `installer/.env` — Environment config (tokens, database names, passwords)
- `installer/WFR25.dbc` — CAN DBC file for WFR25 season data

---

## Grafana

### Injecting/updating a dashboard via API

Do not manually edit provisioned files and restart Grafana. Instead, push directly via the API — it takes effect immediately with no restart needed.

```python
import json, urllib.request, base64

with open('installer/grafana/dashboards/MyDashboard.json') as f:
    dashboard = json.load(f)

payload = json.dumps({
    'dashboard': dashboard,
    'overwrite': True,       # False to create new, True to update existing
    'message': 'describe change here'
}).encode()

req = urllib.request.Request(
    'http://localhost:8087/api/dashboards/db',
    data=payload,
    headers={
        'Content-Type': 'application/json',
        'Authorization': 'Basic ' + base64.b64encode(b'admin:password').decode()
    },
    method='POST'
)
with urllib.request.urlopen(req) as resp:
    print(resp.status, resp.read().decode())
```

- Grafana runs on port `8087`
- Admin credentials: `admin` / `password` (set via `GRAFANA_ADMIN_PASSWORD` in `.env`)
- Dashboard `uid` in the JSON controls the URL: `/d/<uid>/<uid>`
- Set `"id": null` when creating a new dashboard to avoid ID collisions
- Bump `"version"` when updating to avoid Grafana rejecting it as stale

### Dashboard provisioning directory

`installer/grafana/dashboards/` is mounted read-only into Grafana. Files here are auto-provisioned on startup but **changes do not hot-reload** — use the API approach above for live updates.

---

## InfluxDB3 Debugging

### Token and host

```bash
TOKEN="apiv3_dev-influxdb-admin-token"
HOST="http://localhost:8181"
DB="WFR26"
```

### Run queries via docker exec

```bash
docker exec influxdb3 influxdb3 query \
  --token "$TOKEN" \
  --host "$HOST" \
  --database "$DB" \
  "SELECT ..."
```

### Useful debugging queries

**Check what time range has data (narrow window to avoid parquet file limit):**
```bash
docker exec influxdb3 influxdb3 query --token "$TOKEN" --host "$HOST" --database "$DB" \
  "SELECT MIN(time), MAX(time) FROM \"$DB\" WHERE time >= '2025-06-01T00:00:00Z' AND time <= '2025-06-30T00:00:00Z'"
```

**Check a specific signal exists and has values:**
```bash
docker exec influxdb3 influxdb3 query --token "$TOKEN" --host "$HOST" --database "$DB" \
  "SELECT time, \"VCU_INV_Torque_Command\", \"Accel_X\" FROM \"$DB\" \
   WHERE time >= '2025-09-14T00:00:00Z' AND time <= '2025-09-15T00:00:00Z' \
   AND \"VCU_INV_Torque_Command\" IS NOT NULL LIMIT 5"
```

**List all column names (grep for specific signals):**
```bash
docker exec influxdb3 influxdb3 query --token "$TOKEN" --host "$HOST" --database "$DB" \
  "SELECT column_name FROM information_schema.columns WHERE table_name = '$DB'" \
  | grep -i "accel\|gyro\|torque"
```

**Count rows in a time window:**
```bash
docker exec influxdb3 influxdb3 query --token "$TOKEN" --host "$HOST" --database "$DB" \
  "SELECT COUNT(*) FROM \"$DB\" WHERE time >= '2025-09-14T00:00:00Z' AND time <= '2025-09-15T00:00:00Z'"
```

### Parquet file limit error

If you see `Query would exceed file limit of 432 parquet files`, the time range is too wide. Narrow it to a single day or a few hours.

To raise the limit (use cautiously on 8GB VPS — risk of OOM):
add `--query-file-limit <N>` to the `influxdb3 serve` command in `docker-compose.yml`.

---

## Known Good Test Data Windows

Use these when debugging queries, dashboards, or sensor discovery.

### WFR25 (InfluxDB database: `WFR25`)

| Window | Local (America/Toronto) | UTC |
|--------|------------------------|-----|
| Run 1 | 2025-10-04 08:00 – 18:00 EDT | 2025-10-04 12:00 – 22:00 UTC |
| Run 2 | 2025-10-03 17:00 – 20:00 EDT | 2025-10-03 21:00 – 2025-10-04 00:00 UTC |

### WFR26 (InfluxDB database: `WFR26`)

| Window | Local (America/Toronto) | UTC |
|--------|------------------------|-----|
| Run 1 | 2025-09-08 23:21 – 23:23 EDT | 2025-09-09 03:21 – 03:23 UTC |

---

## Wide Schema Notes

- Each CAN frame = one row with all decoded signals as columns (NULLs for signals not in that frame)
- Table name = bucket name (e.g. `WFR26`)
- Grafana SQL queries select signal columns directly: `AVG("SignalName") AS "SignalName"`
- **WFR25 (resolved)**: `Accel_X/Y/Z` and `Gyro_X/Y/Z` originally collapsed into one column in wide schema (blended front/rear). Re-uploaded with updated DBC → now stored as `Front_Accel_X`, `Rear_Accel_X`, etc., matching WFR26 convention.
- See `installer/INFLUXDB3_OPS.md` for full ops runbook (WAL, snapshots, deleting data, OOM notes)
