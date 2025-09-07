# WFR DAQ System - Step-by-Step Installation Guide

## 🎯 Installation Overview

This guide walks you through setting up the complete Western Formula Racing DAQ system using the automated installer. The system provides real-time telemetry collection, analysis, and visualization for race car data.

## 📋 Prerequisites

### System Requirements
- **Operating System**: macOS, Linux, or Windows with WSL2
- **Docker**: Version 20.10+ with Docker Compose
- **Memory**: Minimum 4GB RAM (8GB recommended)
- **Storage**: 2GB free space for containers and data
- **Network**: Ports 8050, 8060, 8085-8087 available

### Required Software
```bash
# Install Docker Desktop
# Visit: https://www.docker.com/products/docker-desktop

# Verify installation
docker --version
docker-compose --version

# Ensure Docker daemon is running
docker ps
```

## 🚀 Step 1: Clone and Navigate

```bash
# Clone the repository
git clone https://github.com/your-org/DAQServerHelpers.git
cd DAQServerHelpers/installer

# Verify you're in the correct directory
ls -la
# Should see: docker-compose.yml, scripts/, grafana/, etc.
```

## ⚡ Step 2: Automated Installation

### Option A: One-Command Install (Recommended)
```bash
# Run the complete automated installer
./scripts/start-daq-system.sh
```

**What happens during this step:**
1. **InfluxDB Startup** (30 seconds)
   - Downloads InfluxDB v2 container image
   - Initializes database with WFR organization
   - Creates "ourCar" bucket for telemetry data
   - Sets up admin credentials

2. **Token Extraction** (15 seconds)
   - Uses Docker CLI to extract all-access token
   - Creates `.env` file with authentication secrets
   - Validates token format and permissions

3. **Service Deployment** (45 seconds)
   - Starts Grafana with auto-configured datasource
   - Launches CAN data receiver service
   - Starts Slack notification bot
   - Deploys lap timing analysis service
   - Serves frontend web application

4. **Health Verification** (15 seconds)
   - Tests all service endpoints
   - Verifies inter-service connectivity
   - Confirms Grafana dashboard access

### Option B: Manual Step-by-Step
If you prefer control over each step:

```bash
# Step 2a: Start InfluxDB only
docker-compose up -d influxdb2
echo "Waiting for InfluxDB initialization..."
sleep 30

# Step 2b: Extract authentication token
./scripts/extract-token-docker.sh

# Step 2c: Start all remaining services
docker-compose up -d
echo "Waiting for services to stabilize..."
sleep 30

# Step 2d: Verify system health
docker ps
curl -f http://localhost:8087/api/health
```

## 🔍 Step 3: Verify Installation

### Check Service Status
```bash
# All containers should show "Up" status
docker ps

# Expected output:
CONTAINER ID   IMAGE                    STATUS
xxxxx          influxdb:2.7             Up 2 minutes
xxxxx          grafana/grafana:latest   Up 1 minute  
xxxxx          car-to-influx            Up 1 minute
xxxxx          slackbot                 Up 1 minute
xxxxx          lappy                    Up 1 minute
xxxxx          nginx:alpine             Up 1 minute
```

### Test Service Endpoints
```bash
# Grafana dashboard
curl -f http://localhost:8087/api/health
echo "Grafana: ✅"

# InfluxDB API
curl -f http://localhost:8086/ping
echo "InfluxDB: ✅"

# Frontend application
curl -f http://localhost:8060
echo "Frontend: ✅"

# CAN data receiver
curl -f http://localhost:8085/health
echo "CAN Receiver: ✅"

# Lap timing service
curl -f http://localhost:8050/health
echo "Lap Timer: ✅"
```

## 🖥️ Step 4: Access the System

### Web Interfaces
Open these URLs in your browser:

1. **📊 Grafana Dashboard**: http://localhost:8087
   - Username: `admin`
   - Password: `YOUR_GRAFANA_PASSWORD`

2. **🗄️ InfluxDB Interface**: http://localhost:8086
   - Username: `admin`
   - Password: `YOUR_INFLUXDB_PASSWORD`

3. **🖥️ Frontend Application**: http://localhost:8060
   - System management and overview

4. **📡 CAN Data Receiver**: http://localhost:8085
   - Real-time CAN frame monitoring

5. **📈 Lap Timing System**: http://localhost:8050
   - Lap analysis and timing data

### First Login to Grafana
1. Navigate to http://localhost:8087
2. Login with credentials above
3. You should see:
   - InfluxDB datasource automatically configured
   - Default dashboards loaded (if any exist in `grafana/dashboards/`)
   - Connection to "ourCar" bucket established

## 🔧 Step 5: Configuration Verification

### Check Token Integration
```bash
# Verify .env file was created
cat .env
# Should show: INFLUXDB_TOKEN=your_extracted_token

# Check Grafana can query InfluxDB
docker logs grafana | grep -i influx
# Should show successful datasource connection
```

### Verify Data Pipeline
```bash
# Check if CAN receiver is processing data
docker logs car-to-influx | tail -10

# Verify InfluxDB is receiving data
curl -H "Authorization: Token $(grep INFLUXDB_TOKEN .env | cut -d= -f2)" \
     "http://localhost:8086/api/v2/query?org=WFR" \
     -d 'from(bucket:"ourCar")|>range(start:-1h)|>limit(n:1)'
```

## 📊 Step 6: Dashboard Setup

### Import Additional Dashboards
1. Open Grafana at http://localhost:8087
2. Navigate to Dashboards → Import
3. Upload JSON files from `grafana/dashboards/`
4. Configure dashboard variables as needed

### Create Your First Query
1. Create new dashboard
2. Add panel
3. Select "InfluxDB_WFR" datasource
4. Use Flux query language:
```flux
from(bucket: "ourCar")
  |> range(start: -1h)
  |> filter(fn: (r) => r["_measurement"] == "telemetry")
  |> filter(fn: (r) => r["_field"] == "speed")
```

## 🔔 Step 7: Slack Integration

### Configure Slack Bot
1. Edit `docker-compose.yml`
2. Add your Slack bot token:
```yaml
environment:
  SLACK_BOT_TOKEN: "xoxb-your-bot-token-here"
```
3. Restart the slackbot service:
```bash
docker-compose restart slackbot
```

## 🐛 Troubleshooting Common Issues

### Issue: Services Won't Start
```bash
# Check for port conflicts
lsof -i :8087  # Grafana port
lsof -i :8086  # InfluxDB port

# Check Docker resources
docker system df
docker system prune  # If needed

# Restart with fresh containers
docker-compose down -v
docker-compose up -d
```

### Issue: Token Extraction Fails
```bash
# Check InfluxDB logs
docker logs influxdb2

# Manual token extraction
open http://localhost:8086
# Login → Data → API Tokens → Generate API Token (All Access)
# Add to .env: INFLUXDB_TOKEN=your_manual_token
```

### Issue: Grafana Can't Connect to InfluxDB
```bash
# Verify network connectivity
docker exec grafana ping influxdb2

# Check datasource configuration
docker exec grafana cat /etc/grafana/provisioning/datasources/influxdb.yml

# Test token manually
TOKEN=$(grep INFLUXDB_TOKEN .env | cut -d= -f2)
curl -H "Authorization: Token $TOKEN" http://localhost:8086/api/v2/buckets
```

### Issue: No Data in Dashboards
```bash
# Check if CAN receiver is running
docker logs car-to-influx

# Verify data is reaching InfluxDB
docker exec influxdb2 influx query 'from(bucket:"ourCar")|>range(start:-1h)|>count()'

# Check for DBC file issues
docker exec car-to-influx ls -la *.dbc
```

## 🔄 Maintenance Commands

### Daily Operations
```bash
# Check system status
docker ps
docker-compose logs -f

# Restart specific service
docker-compose restart SERVICE_NAME

# View resource usage
docker stats
```

### Update System
```bash
# Pull latest images
docker-compose pull

# Restart with new images
docker-compose up -d
```

### Backup Data
```bash
# Backup InfluxDB
docker exec influxdb2 influx backup /backup
docker cp influxdb2:/backup ./influxdb-backup-$(date +%Y%m%d)

# Backup Grafana dashboards
cp -r grafana/dashboards ./grafana-backup-$(date +%Y%m%d)
```

## ✅ Installation Complete!

Your WFR DAQ system is now fully operational. You should have:

- ✅ All services running and healthy
- ✅ Grafana connected to InfluxDB automatically
- ✅ Token authentication working
- ✅ Web interfaces accessible
- ✅ Data pipeline ready for telemetry
- ✅ Slack notifications configured (if token provided)

## 📞 Next Steps

1. **Configure your CAN interface** to send data to port 8085
2. **Create custom Grafana dashboards** for your specific telemetry needs
3. **Set up Slack channels** for race day notifications
4. **Test the system** with sample CAN data
5. **Train your team** on using the web interfaces

## 🆘 Support

For additional help:
- Review logs: `docker logs CONTAINER_NAME`
- Check documentation in `TOKEN_EXTRACTION_README.md`
- Contact the DAQ Team Lead
- Review Western Formula Racing DAQ documentation

---

**Happy Racing! 🏎️💨**
