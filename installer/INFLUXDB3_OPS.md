# InfluxDB 3 Operations Guide

This covers operational commands for managing the InfluxDB 3 Core instance running in Docker.

---

## Setup

All commands assume you're in the installer directory:

```bash
cd ~/projects/daq-server-components/installer
```

The admin token is in `influxdb3-admin-token.json`. Set it as a variable for convenience:

```bash
TOKEN="apiv3_dev-influxdb-admin-token"
```

The data volume is mounted at:

```
/var/lib/docker/volumes/installer_influxdb3-data/_data/influxdb3-node/
```

---

## Querying

### List all databases

```bash
docker exec influxdb3 influxdb3 show databases \
  --token "$TOKEN" \
  --host http://localhost:8181
```

### List tables in a database

```bash
docker exec influxdb3 influxdb3 query \
  --token "$TOKEN" \
  --host http://localhost:8181 \
  --database WFR26 \
  "SHOW TABLES"
```

### Count rows in a table

```bash
docker exec influxdb3 influxdb3 query \
  --token "$TOKEN" \
  --host http://localhost:8181 \
  --database WFR26 \
  "SELECT COUNT(*) FROM WFR26"
```

### Check parquet file stats per table

Shows file count, total bytes, and row count for all persisted data:

```bash
docker exec influxdb3 influxdb3 query \
  --token "$TOKEN" \
  --host http://localhost:8181 \
  --database WFR26 \
  "SELECT table_name, COUNT(*) as file_count, SUM(size_bytes) as total_bytes, SUM(row_count) as total_rows FROM system.parquet_files GROUP BY table_name"
```

---

## Disk usage

The data volume is owned by root inside Docker, so use `sudo` for all file operations. **Never use glob expansion (`*`) with sudo** — the shell expands globs before sudo runs, and if the current user lacks read permission the glob won't expand, silently doing nothing. Always use `find` instead.

### Check size of each database on disk

```bash
DATA=/var/lib/docker/volumes/installer_influxdb3-data/_data/influxdb3-node
sudo du -sh $DATA/dbs/*/
```

### Count WAL, snapshot, and parquet files

```bash
DATA=/var/lib/docker/volumes/installer_influxdb3-data/_data/influxdb3-node
echo "WAL:       $(sudo find $DATA/wal -type f | wc -l) files"
echo "Snapshots: $(sudo find $DATA/snapshots -type f | wc -l) files"
echo "Parquet:   $(sudo find $DATA/dbs -name '*.parquet' | wc -l) files"
```

---

## Data structure

```
influxdb3-node/
├── catalog/          # Schema/table definitions (source of truth)
│   └── v2/
│       └── logs/     # Catalog write-ahead log
├── dbs/              # Parquet data files, organised by database/table/time
│   └── WFR26-6/
│       └── WFR26-0/
│           └── 2026-03-16/
│               └── 00-00/
│                   └── 0000000001.parquet
├── snapshots/        # Index of which parquet files exist (snapshot N.info.json)
├── wal/              # Unsnapshotted writes (00000XXXXXX.wal)
└── db-indices/
```

**Important:** The `snapshots/` directory is InfluxDB's index of persisted parquet files. If you delete snapshot files without also deleting the corresponding parquet files, InfluxDB will lose track of the data — it will still exist on disk but be invisible to queries. Always delete snapshots and parquet files together.

---

## Deleting data

Always stop InfluxDB before deleting data files.

### Delete a specific database's parquet data

This removes the data but leaves the database definition in the catalog. InfluxDB will think the database is empty.

```bash
docker compose stop influxdb3

DATA=/var/lib/docker/volumes/installer_influxdb3-data/_data/influxdb3-node

# Delete parquet files for a specific database folder (check folder name with sudo ls $DATA/dbs/)
sudo find $DATA/dbs/WFR25w-5 -type f -delete
sudo rm -rf $DATA/dbs/WFR25w-5

# Also clear snapshots so the catalog index is consistent
sudo find $DATA/snapshots -type f -delete

docker compose up -d influxdb3
```

### Delete WAL files (clear unsnapshotted writes)

Only do this if you're OK losing writes that haven't been persisted to parquet yet:

```bash
docker compose stop influxdb3
DATA=/var/lib/docker/volumes/installer_influxdb3-data/_data/influxdb3-node
sudo find $DATA/wal -type f -delete
docker compose up -d influxdb3
```

### Full wipe (completely fresh start)

Deletes all data, schema, and history. InfluxDB will initialise from scratch:

```bash
docker compose stop influxdb3

DATA=/var/lib/docker/volumes/installer_influxdb3-data/_data/influxdb3-node
sudo find $DATA/dbs      -type f -delete && sudo find $DATA/dbs -mindepth 1 -type d -delete
sudo find $DATA/snapshots -type f -delete
sudo find $DATA/wal       -type f -delete
sudo find $DATA/catalog   -type f -delete

docker compose up -d influxdb3
```

---

## Restarting and monitoring

### Restart InfluxDB

```bash
docker compose up -d influxdb3
```

### Check health and restart count

```bash
docker ps --filter name=influxdb3 --format "{{.Names}}\t{{.Status}}"
docker inspect influxdb3 --format 'Restarts: {{.RestartCount}}, OOMKilled: {{.State.OOMKilled}}'
```

### Watch logs for WAL replay / errors

```bash
docker logs influxdb3 --tail 50 -f
```

### Check for OOM kills in kernel journal

```bash
journalctl -k --since "1 hour ago" | grep -i "oom\|killed process"
```

---

## Configuration (docker-compose.yml)

Key flags in the `influxdb3 serve` command:

| Flag | Current value | Notes |
|---|---|---|
| `--wal-snapshot-size` | `10` | How many WAL periods before snapshotting. Lower = more frequent snapshots = less memory needed on restart. Wide schema needs a low value. |
| `--snapshotted-wal-files-to-keep` | `10` | How many old WAL files to retain after snapshotting. |

Key healthcheck settings:

| Setting | Current value | Notes |
|---|---|---|
| `start_period` | `300s` | How long before health check failures count. Must be longer than WAL replay time. |
| `interval` | `10s` | |
| `retries` | `5` | |

### Why `--wal-snapshot-size 10` matters for wide schema

Wide schema rows are much larger than narrow schema rows — each row contains all signals rather than one. This means the in-memory WAL buffer fills up much faster. With a high `--wal-snapshot-size` (e.g., 100), InfluxDB accumulates too much data before snapshotting, causing:

- Gigabytes of data replayed into memory on every restart
- OOM kills on an 8 GB VPS, even with 8 GB swap
- Restart loops where the process dies mid-persistence

Setting `--wal-snapshot-size 10` makes InfluxDB snapshot 10× more frequently, keeping the replay memory footprint small.

---

## Memory notes (8 GB VPS)

- Wide schema WAL replay can peak at 6–7 GB RSS + several GB swap simultaneously
- The kernel OOM killer will fire if influxdb3 + swap usage exceeds available RAM + swap
- `docker inspect influxdb3 --format '{{.State.OOMKilled}}'` reports Docker-level OOM, but kernel-level OOM kills show as exit code `0` — check `journalctl -k | grep oom` instead
- If InfluxDB is crash-looping with exit code `0`, always check the kernel journal first
