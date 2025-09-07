# WFR DAQ System - Installation Guide

## 🚀 Quick Start

**With Slack Integration:**
```bash
cd installer
./scripts/start-daq-system-no-slack.sh
```

### Step 2: Manual Installation Steps

If you prefer manual control or troubleshooting:

**Full Installation:**
```bash
docker-compose up -d influxdb2
sleep 15
./scripts/extract-token-docker.sh
docker-compose up -d
```

**Minimal Installation (no Slack):**
```bash
docker-compose -f docker-compose.no-slack.yml up -d
```

**Without Slack (Minimal Setup):**
```bash
cd installer
./scripts/start-daq-system-no-slack.sh
```
sleep 15
./scripts/extract-token-docker.sh
docker-compose -f docker-compose.no-slack.yml up -d
```GitHub/DAQServerHelpers/installer
./scripts/start-daq-system.sh
```

**Without Slack (Minimal Setup):**
```bash
cd installer
./scripts/start-daq-system-no-slack.sh
```

## 📋 System Overview

The WFR DAQ (Data Acquisition) system is a containerized solution for collecting, storing, and visualizing Formula Racing car telemetry data. This installer sets up a complete data pipeline including:

- **InfluxDB v2**: Time-series database for telemetry storage
- **Grafana**: Real-time dashboard and visualization platform  
- **CAN Data Receiver**: Processes CAN bus frames from the race car
- **Slack Bot**: Provides team notifications and data analysis
- **Lap Timing System**: Tracks and analyzes lap performance
- **Frontend Application**: Web interface for system management

## 🏗️ Installation Process

### Option A: Full Installation (with Slack)
```bash
./scripts/start-daq-system.sh
```

### Option B: Minimal Installation (no Slack)
```bash
./scripts/start-daq-system-no-slack.sh
```

Both options provide the same core functionality, but the minimal installation excludes:
- Slack bot container
- Slack startup notifications
- Slack-related dependencies

**What happens during startup:**

1. **InfluxDB Initialization** (30 seconds)
   - Starts InfluxDB container with persistent storage
   - Waits for database to become ready
   - Configures organization "WFR" and bucket "ourCar"

2. **Token Extraction** (Automatic)
   - Uses Docker-based CLI to extract all-access token
   - Creates `.env` file with `INFLUXDB_TOKEN`
   - Falls back to Python/Bash API methods if needed

3. **Service Deployment** (30 seconds)
   - Starts all services in dependency order
   - Establishes `datalink` network for inter-container communication
   - Applies resource limits and restart policies

4. **Startup Data Loading** (Automatic)
   - Loads any CSV files from `startup-data/` directory
   - Uses DBC file to decode CAN messages
   - Streams historical telemetry data to InfluxDB
   - Provides real-time progress feedback

5. **Configuration Provisioning** (Automatic)
   - Grafana auto-configures InfluxDB datasource
   - Loads default dashboards from `grafana/dashboards/`
   - Sets up admin user: `admin` / `turbo-charged-plotting-machine`

6. **Health Verification & Notifications** (15 seconds)
   - Tests all service endpoints
   - Verifies Grafana ↔ InfluxDB connectivity
   - Reports system status and access URLs
   - Sends comprehensive status to Slack (if configured)

### Step 2: Access Services

After successful installation, access these URLs:

- **📊 Grafana Dashboard**: http://localhost:8087
- **🗄️ InfluxDB Interface**: http://localhost:8086  
- **🖥️ Frontend Application**: http://localhost:8060
- **📡 CAN Data Receiver**: http://localhost:8085
- **📈 Lap Timing System**: http://localhost:8050

## 🔧 Manual Installation Steps

If you prefer manual control or troubleshooting:

### 1. Start Core Database
```bash
docker-compose up -d influxdb2
sleep 15  # Wait for initialization
```

### 2. Extract Authentication Token
```bash
./scripts/extract-token-docker.sh
```

### 3. Start All Services
```bash
docker-compose up -d
```

### 4. Verify System Health
```bash
docker ps  # Check container status
docker logs grafana  # Check Grafana logs
curl http://localhost:8087/api/health  # Test Grafana
```

## 📁 Project Structure

```
installer/
├── docker-compose.yml           # Container orchestration
├── .env                        # Auto-generated secrets
├── README.md                   # This file
├── TOKEN_EXTRACTION_README.md  # Token automation docs
│
├── scripts/                    # Automation scripts
│   ├── start-daq-system.sh    # Main installer
│   ├── extract-token-docker.sh # Docker-based token extraction  
│   ├── extract-influx-token.py # Python API extraction
│   └── extract-influx-token.sh # Bash API extraction
│
├── startup-data/              # Historical telemetry data
│   ├── *.csv                  # CAN data files (auto-loaded)
│   ├── WFR25.dbc              # CAN database file
│   └── helper.py              # Data processing utilities
│
├── startup-data-loader/       # Data ingestion container
│   ├── Dockerfile
│   ├── load_data.py           # CSV to InfluxDB streamer
│   ├── requirements.txt
│   └── README.md
│
├── grafana/                    # Grafana configuration
│   ├── provisioning/
│   │   ├── datasources/        # Auto InfluxDB connection
│   │   └── dashboards/         # Dashboard provider config
│   ├── dashboards/             # JSON dashboard files
│   └── README.md               # Dashboard import guide
│
├── car-to-influx/             # CAN data processor
│   ├── Dockerfile
│   ├── listener.py
│   ├── WFR25-f772b40.dbc      # CAN database file
│   └── templates/
│
├── slackbot/                  # Team notifications
│   ├── Dockerfile
│   ├── slack_bot.py
│   └── requirements.txt
│
├── lappy/                     # Lap timing analysis
│   ├── Dockerfile
│   ├── lap.py
│   └── requirements.txt
│
└── frontend-build/            # Web interface
    ├── index.html
    └── assets/
```

## 🔒 Security & Credentials

### Default Accounts
- **Grafana**: `admin` / `turbo-charged-plotting-machine`
- **InfluxDB**: `admin` / `turbo-charged-falcon-machine`
- **Organization**: `WFR`
- **Bucket**: `ourCar`

### Token Management
- InfluxDB tokens are automatically extracted and rotated
- Slack tokens are configured in `docker-compose.yml`
- All secrets stored in `.env` file (git-ignored)
- Slack webhook URL can be set via `SLACK_WEBHOOK_URL` environment variable

## � Slack Integration

### Automatic Startup Notifications
The system automatically sends a comprehensive status report to Slack after startup, including:
- Service status for all containers
- Connectivity test results  
- Service URLs and access information
- Data loading completion status

### Configuration
```bash
# Add to .env file:
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

### Manual Slack Bot Setup
```bash
# Edit docker-compose.yml:
environment:
  SLACK_BOT_TOKEN: "xoxb-your-bot-token-here"
  SLACK_APP_TOKEN: "xapp-your-app-token-here"

# Restart slackbot service:
docker-compose restart slackbot
```

## �🐛 Troubleshooting

### Common Issues

**Services won't start:**
```bash
docker-compose logs SERVICE_NAME
docker system prune  # Clean up resources
```

**Token extraction fails:**
```bash
# Manual token creation
open http://localhost:8086
# Login → Data → API Tokens → Generate API Token
# Copy token to .env file
```

**Grafana can't connect to InfluxDB:**
```bash
docker logs grafana
# Check datasource configuration
# Verify token in .env file
```

**Port conflicts:**
```bash
lsof -i :8087  # Check what's using Grafana port
# Modify ports in docker-compose.yml if needed
```

## 📈 Data Flow

1. **Historical Data Loading** → CSV files in `startup-data/` automatically loaded on first start
2. **Race Car** → CAN Bus frames
3. **CAN Receiver** (port 8085) → Processes frames using DBC file
4. **InfluxDB** (port 8086) → Stores time-series data
5. **Grafana** (port 8087) → Visualizes real-time telemetry
6. **Slack Bot** → Sends race notifications
7. **Frontend** (port 8060) → System management interface

## 🔄 Maintenance

### Daily Operations
```bash
# View system status
docker ps

# Monitor logs  
docker-compose logs -f

# Restart specific service
docker-compose restart SERVICE_NAME

# Update containers
docker-compose pull && docker-compose up -d
```

### Data Backup
```bash
# Backup InfluxDB data
docker exec influxdb2 influx backup /backup
docker cp influxdb2:/backup ./influxdb-backup-$(date +%Y%m%d)

# Backup Grafana dashboards
cp -r grafana/dashboards ./grafana-backup-$(date +%Y%m%d)
```

## 📞 Support

For issues or questions:
- Check logs: `docker logs CONTAINER_NAME`
- Review documentation in `TOKEN_EXTRACTION_README.md`
- Contact DAQ Team Lead
- Review Western Formula Racing DAQ documentation
