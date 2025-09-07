#!/bin/bash

# WFR DAQ System Automated Startup Script - No Slack Version
# Handles InfluxDB token extraction and Grafana auto-configuration

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "🏁 WFR DAQ System - Automated Startup (No Slack)"
echo "=============================================="
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
docker-compose -f docker-compose.no-slack.yml up -d influxdb2

echo "⏳ Waiting for InfluxDB to fully initialize..."
sleep 15

# Token extraction (same as main script)
echo "🐳 Using Docker-based token extraction..."
if bash scripts/extract-token-docker.sh; then
    echo "✅ Token extraction successful via Docker method!"
    TOKEN_EXTRACTED=true
else
    echo "⚠️ Token extraction failed"
    TOKEN_EXTRACTED=false
fi

if [ "$TOKEN_EXTRACTED" != "true" ]; then
    echo "❌ CRITICAL ERROR: Token extraction failed!"
    echo "Please check InfluxDB logs and try again."
    exit 1
fi

echo ""
echo "🚀 Step 2: Starting core services..."
echo "Starting DAQ stack without Slack components..."

# Start all services except Slack
docker-compose -f docker-compose.no-slack.yml up -d influxdb2 grafana frontend car-to-influx lappy

echo ""
echo "⏳ Step 3: Waiting for services to stabilize..."
sleep 15

echo ""
echo "📊 Step 4: Loading startup data..."
if [ -d "startup-data" ] && [ -n "$(ls -A startup-data/*.csv 2>/dev/null)" ]; then
    echo "📂 Found CSV files, starting data loader..."
    docker-compose -f docker-compose.no-slack.yml up startup-data-loader
    
    exit_code=$(docker wait startup-data-loader 2>/dev/null || echo "1")
    if [ "$exit_code" = "0" ]; then
        echo "✅ Startup data loaded successfully!"
    else
        echo "⚠️ Data loader completed with warnings"
    fi
else
    echo "📂 No CSV files found, skipping data loading"
fi

echo ""
echo "🔍 Step 5: Service Status Check..."
echo "Service Status:"
echo "---------------"

services=("influxdb2" "grafana" "frontend" "car-to-influx" "lappy" "startup-data-loader")

for service in "${services[@]}"; do
    if [ "$service" = "startup-data-loader" ]; then
        if container_exists "$service"; then
            exit_code=$(docker inspect startup-data-loader --format='{{.State.ExitCode}}' 2>/dev/null || echo "unknown")
            if [ "$exit_code" = "0" ]; then
                echo "✅ $service: SERVICE COMPLETE - STOPPED"
            else
                echo "⚠️ $service: COMPLETED WITH EXIT CODE $exit_code"
            fi
        else
            echo "❓ $service: NOT NEEDED (NO DATA FILES)"
        fi
    elif container_running "$service"; then
        echo "✅ $service: RUNNING"
    elif container_exists "$service"; then
        echo "⚠️ $service: EXISTS BUT NOT RUNNING"
    else
        echo "❌ $service: NOT FOUND"
    fi
done

echo ""
echo "🌐 Service URLs:"
echo "----------------"
echo "📊 Grafana Dashboard: http://localhost:8087"
echo "   └─ Username: admin"
echo "   └─ Password: turbo-charged-plotting-machine"
echo ""
echo "🗄️ InfluxDB Interface: http://localhost:8086"
echo "   └─ Username: admin"
echo "   └─ Password: turbo-charged-falcon-machine"
echo ""
echo "🖥️ Frontend Application: http://localhost:8060"
echo "📡 CAN Data Receiver: http://localhost:8085"
echo "📈 Lap Timing System: http://localhost:8050"

echo ""
echo "✅ DAQ System startup complete!"
echo ""
echo "📝 Next Steps:"
echo "1. Verify Grafana can connect to InfluxDB (should be automatic)"
echo "2. Check the default dashboard in Grafana"
echo "3. Test CAN data ingestion endpoints"
echo "4. Monitor system logs: docker-compose -f docker-compose.no-slack.yml logs -f"

# Connectivity test
echo ""
echo "🔬 Quick Connectivity Test:"
echo "--------------------------"

if curl -s "http://localhost:8086/health" >/dev/null 2>&1; then
    echo "✅ InfluxDB: Accessible"
else
    echo "❌ InfluxDB: Not accessible"
fi

if curl -s "http://localhost:8087/api/health" >/dev/null 2>&1; then
    echo "✅ Grafana: Accessible"
else
    echo "❌ Grafana: Not accessible"
fi

if curl -s "http://localhost:8060" >/dev/null 2>&1; then
    echo "✅ Frontend: Accessible"
else
    echo "❌ Frontend: Not accessible"
fi

echo ""
echo "🎯 System Ready for Data Acquisition!"
echo "💡 Note: Slack integration disabled - no notifications will be sent"
