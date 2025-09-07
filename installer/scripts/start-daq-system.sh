#!/bin/bash

# WFR DAQ System Automated Startup Script
# Handles InfluxDB token extraction and Grafana auto-configuration

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "🏁 WFR DAQ System - Automated Startup"
echo "=================================="
echo ""

cd "$PROJECT_DIR"

# Function to check if container is running
container_running() {
    docker ps --format "table {{.Names}}" | grep -q "^$1$"
}

# Function to check if container exists
container_exists() {
    docker ps -a --format "table {{.Names}}" | grep -q "^$1$"
}

echo "🔧 Step 1: Starting InfluxDB..."
echo "Starting InfluxDB container first to generate tokens..."

# Start only InfluxDB first
docker-compose up -d influxdb2

echo "⏳ Waiting for InfluxDB to fully initialize..."
sleep 15

# Method 1: Docker-based extraction (Most Reliable)
echo "🐳 Using Docker-based token extraction..."
if bash scripts/extract-token-docker.sh; then
    echo "✅ Token extraction successful via Docker method!"
    TOKEN_EXTRACTED=true
else
    echo "⚠️  Docker-based extraction failed, trying Python method..."
    TOKEN_EXTRACTED=false
fi

# Method 2: Python API script (Fallback)
if [ "$TOKEN_EXTRACTED" != "true" ] && command -v python3 &> /dev/null; then
    echo "🐍 Using Python script to extract token..."
    if python3 scripts/extract-influx-token.py; then
        echo "✅ Token extraction successful via Python!"
        TOKEN_EXTRACTED=true
    else
        echo "⚠️  Python script failed, trying bash method..."
        TOKEN_EXTRACTED=false
    fi
fi

# Method 3: Bash API script (Final fallback)
if [ "$TOKEN_EXTRACTED" != "true" ] && command -v jq &> /dev/null && command -v curl &> /dev/null; then
    echo "🔧 Using bash script to extract token..."
    if bash scripts/extract-influx-token.sh; then
        echo "✅ Token extraction successful via bash!"
        TOKEN_EXTRACTED=true
    else
        echo "❌ Bash script failed"
        TOKEN_EXTRACTED=false
    fi
fi

# Method 4: Complete failure - abort with helpful message
if [ "$TOKEN_EXTRACTED" != "true" ]; then
    echo ""
    echo "❌ CRITICAL ERROR: All token extraction methods failed!"
    echo ""
    echo "🛠️  Manual steps to resolve:"
    echo "1. Check InfluxDB logs: docker logs influxdb2"
    echo "2. Access InfluxDB web UI: http://localhost:8086"
    echo "3. Login with: admin / YOUR_INFLUXDB_PASSWORD"
    echo "4. Generate a new token in the UI"
    echo "5. Create .env file with: INFLUXDB_TOKEN=your_token_here"
    echo ""
    echo "⚠️  Exiting startup process..."
    exit 1
fi

echo ""
echo "🚀 Step 2: Starting all services..."
echo "Starting the complete DAQ stack..."

# Start all services except data loader first
docker-compose up -d influxdb2 grafana frontend car-to-influx slackbot lappy

echo ""
echo "⏳ Step 3: Waiting for services to stabilize..."
sleep 15

echo ""
echo "� Step 4: Loading startup data..."
echo "Starting data loader to populate InfluxDB with initial data..."

# Check if startup data exists
if [ -d "startup-data" ] && [ -n "$(ls -A startup-data/*.csv 2>/dev/null)" ]; then
    echo "📂 Found CSV files in startup-data/, starting data loader..."
    docker-compose up startup-data-loader
    
    # Check if data loader completed successfully
    exit_code=$(docker wait startup-data-loader 2>/dev/null || echo "1")
    if [ "$exit_code" = "0" ]; then
        echo "✅ Startup data loaded successfully!"
    else
        echo "⚠️  Startup data loader completed with warnings/errors"
        echo "📋 Check logs: docker logs startup-data-loader"
    fi
else
    echo "📂 No CSV files found in startup-data/, skipping data loading"
fi

echo ""
echo "�🔍 Step 5: Service Status Check..."

# Check service status
echo "Service Status:"
echo "---------------"

services=("influxdb2" "grafana" "frontend" "car-to-influx" "slackbot" "lappy" "startup-data-loader")

for service in "${services[@]}"; do
    if [ "$service" = "startup-data-loader" ]; then
        # Data loader is expected to exit after completing
        if container_exists "$service"; then
            exit_code=$(docker inspect startup-data-loader --format='{{.State.ExitCode}}' 2>/dev/null || echo "unknown")
            if [ "$exit_code" = "0" ]; then
                echo "✅ $service: COMPLETED SUCCESSFULLY"
            else
                echo "⚠️  $service: COMPLETED WITH EXIT CODE $exit_code"
            fi
        else
            echo "❓ $service: NOT RUN"
        fi
    elif container_running "$service"; then
        echo "✅ $service: RUNNING"
    elif container_exists "$service"; then
        echo "⚠️  $service: EXISTS BUT NOT RUNNING"
    else
        echo "❌ $service: NOT FOUND"
    fi
done

echo ""
echo "🌐 Step 6: Service URLs:"
echo "----------------------"
echo "📊 Grafana Dashboard: http://3.98.181.12:8087"
echo "   └─ Username: admin"
echo "   └─ Password: YOUR_GRAFANA_PASSWORD"
echo ""
echo "🗄️  InfluxDB Interface: http://3.98.181.12:8086"
echo "   └─ Username: admin"
echo "   └─ Password: YOUR_INFLUXDB_PASSWORD"
echo ""
echo "🖥️  Frontend Application: http://3.98.181.12:8060"
echo "📡 CAN Data Receiver: http://3.98.181.12:8085"
echo "📈 Lap Timing System: http://3.98.181.12:8050"

echo ""
echo "✅ DAQ System startup complete!"
echo ""
echo "📝 Next Steps:"
echo "1. Verify Grafana can connect to InfluxDB (should be automatic)"
echo "2. Check the default dashboard in Grafana"
echo "3. Test CAN data ingestion endpoints"
echo "4. Monitor system logs: docker-compose logs -f"

# Optional: Run a quick connectivity test
echo ""
echo "🔬 Quick Connectivity Test:"
echo "--------------------------"

# Test InfluxDB
if curl -s "http://localhost:8086/health" >/dev/null 2>&1; then
    echo "✅ InfluxDB: Accessible"
else
    echo "❌ InfluxDB: Not accessible"
fi

# Test Grafana
if curl -s "http://localhost:8087/api/health" >/dev/null 2>&1; then
    echo "✅ Grafana: Accessible"
else
    echo "❌ Grafana: Not accessible"
fi

# Test Frontend
if curl -s "http://localhost:8060" >/dev/null 2>&1; then
    echo "✅ Frontend: Accessible"
else
    echo "❌ Frontend: Not accessible"
fi

echo ""
echo "🎯 System Ready for Data Acquisition!"
