# Simplified Docker Compose Setup (No Shell Scripts)

## Overview
This DAQ system now uses a **preset InfluxDB admin token** instead of dynamically extracting tokens via shell scripts. This simplifies deployment and makes it fully `docker-compose` based.

## Key Changes

### 1. Preset Admin Token
- InfluxDB is initialized with a preset admin token specified in the `.env` file
- All services use the same `INFLUXDB_ADMIN_TOKEN` environment variable
- No need for token extraction scripts

### 2. Environment Variable
Add this to your `.env` file:
```bash
# InfluxDB Admin Token (preset for all services to use)
# IMPORTANT: Change this in production for security!
INFLUXDB_ADMIN_TOKEN=wfr-admin-token-change-in-production
```

### 3. Services Using the Token
The following services now reference `INFLUXDB_ADMIN_TOKEN`:
- `influxdb2` - Sets the admin token during initialization via `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN`
- `influxdb3` - Uses the token for API access
- `grafana` - Uses the token to connect to InfluxDB datasource
- `car-to-influx` - Uses the token to write CAN data
- `startup-data-loader` - Uses the token to load initial data
- `file-uploader` - Uses the token to upload files

## Quick Start

### 1. Set Up Environment
```bash
cd installer
cp .env.example .env
# Edit .env and set INFLUXDB_ADMIN_TOKEN to a secure value
```

### 2. Start All Services
```bash
docker-compose up -d
```

### 3. Verify Services
```bash
# Check all containers are running
docker ps

# Check InfluxDB health
curl http://localhost:8086/health

# Check Grafana health
curl http://localhost:8087/api/health
```

## Production Security

⚠️ **Important**: The default token `wfr-admin-token-change-in-production` should be changed for production deployments!

### Generate a Secure Token
```bash
# Generate a random secure token
openssl rand -base64 32

# Or use Python
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Update your `.env` file:
```bash
INFLUXDB_ADMIN_TOKEN=your-secure-random-token-here
```

## Removed Files/Features

### Shell Scripts No Longer Needed
- `extract-influx-token.sh` - Token extraction logic removed
- Token validation steps removed from startup script

### Simplified Startup Process
No more:
- Starting InfluxDB separately
- Running token extraction scripts
- Restarting services with new tokens

## Accessing Services

All services use the same admin token configured in `.env`:

### InfluxDB Web UI
- URL: http://localhost:8086
- Username: `admin`
- Password: `${INFLUXDB_INIT_PASSWORD}` (from .env)
- API Token: `${INFLUXDB_ADMIN_TOKEN}` (from .env)

### Grafana
- URL: http://localhost:8087
- Username: `admin`
- Password: `${GRAFANA_ADMIN_PASSWORD}` (from .env)
- InfluxDB datasource is auto-configured with the admin token

### Other Services
- Frontend: http://localhost:8060
- CAN Data Receiver: http://localhost:8085
- Lap Timing: http://localhost:8050
- File Uploader: http://localhost:8084

## Troubleshooting

### Token Not Working
If you change the token after initial setup:
```bash
# Remove all volumes to reset InfluxDB
docker compose down --rmi local --volumes --remove-orphans

# Start fresh with new token
docker-compose up -d
```

### Service Can't Connect to InfluxDB
1. Check the token matches in `.env`
2. Verify InfluxDB is running: `docker ps | grep influxdb2`
3. Check InfluxDB logs: `docker logs influxdb2`
4. Restart the service: `docker-compose restart <service-name>`

### View All Environment Variables
```bash
docker-compose config
```


## Benefits of This Approach

✅ **Simpler**: No shell scripts needed, pure docker-compose  
✅ **Reproducible**: Same token across all environments  
✅ **Faster**: No waiting for token extraction  
✅ **Predictable**: Token known before services start  
✅ **Portable**: Works on any platform with Docker  
✅ **CI/CD Friendly**: Easy to inject tokens via environment

## Reference

- Docker Compose file: `docker-compose.yml`
- Environment template: `.env.example`
