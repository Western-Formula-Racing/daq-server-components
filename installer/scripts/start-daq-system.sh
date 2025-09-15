#!/bin/bash

# WFR DAQ System Automated Startup Script
# Handles InfluxDB token extraction and Grafana auto-configuration

set -e

export $(grep -vE '^\s*#|^\s*$' .env)

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
sleep 10


# Run the single most reliable token extraction script
echo "🐳 Using robust Docker-based token extraction..."

if ! bash scripts/extract-influx-token.sh; then
    echo ""
    echo "❌ CRITICAL ERROR: Token extraction failed!"
    echo "🛠️  Manual steps to resolve:"
    echo "1. Check your INFLUXDB_PASSWORD in your environment or .env file."
    echo "2. Check InfluxDB logs: docker logs influxdb2"
    echo "3. If you are stuck, reset the database with 'docker-compose down -v'"
    echo ""
    echo "⚠️  Exiting startup process..."
    exit 1
fi

echo "✅ Token extraction successful!"

echo ""
echo "🚀 Step 2: Starting all services..."
echo "Starting the complete DAQ stack..."

# Start all services except data loader first
docker-compose up -d influxdb2 grafana frontend car-to-influx slackbot lappy file-uploader

echo ""
echo "⏳ Step 3: Waiting for services to stabilize..."
sleep 5

echo ""
echo "� Step 4: Loading startup data..."
echo "Starting data loader to populate InfluxDB with initial data..."


# Check if startup data exists in startup-data-loader/data
if [ -d "startup-data-loader/data" ] && [ -n "$(ls -A startup-data-loader/data/*.csv 2>/dev/null)" ]; then
    echo "📂 Found CSV files in startup-data-loader/data/, starting data loader..."
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
    echo "📂 No CSV files found in startup-data-loader/data/, skipping data loading"
fi

echo ""
echo "�🔍 Step 5: Service Status Check..."

# Check service status
echo "Service Status:"
echo "---------------"

services=("influxdb2" "grafana" "frontend" "car-to-influx" "slackbot" "lappy" "startup-data-loader" "file-uploader")

for service in "${services[@]}"; do
    if [ "$service" = "startup-data-loader" ]; then
        # Data loader is expected to exit after completing
        if container_exists "$service"; then
            exit_code=$(docker inspect startup-data-loader --format='{{.State.ExitCode}}' 2>/dev/null || echo "unknown")
            if [ "$exit_code" = "0" ]; then
                echo "✅ $service: SERVICE COMPLETE - STOPPED"
            else
                echo "⚠️  $service: COMPLETED WITH EXIT CODE $exit_code"
            fi
        else
            echo "❓ $service: NOT NEEDED (NO DATA FILES)"
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
echo "   └─ Password: ${GRAFANA_ADMIN_PASSWORD:-your-grafana-password-here}"
echo ""
echo "🗄️  InfluxDB Interface: http://3.98.181.12:8086"
echo "   └─ Username: admin"
echo "   └─ Password: ${INFLUXDB_PASSWORD:-your-influxdb-password-here}"
echo ""
echo "🖥️  Frontend Application: http://3.98.181.12:8060"
echo "📡 CAN Data Receiver: http://3.98.181.12:8085"
echo "📈 Lap Timing System: http://3.98.181.12:8050"
echo "📂 File Uploader System: http://3.98.181.12:8084"

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

# Function to send Slack notification
send_slack_notification() {
    local message="$1"
    
    # Check for Slack webhook URL (priority: env var, then default)
    local webhook_url="${SLACK_WEBHOOK_URL:-https://hooks.slack.com/services/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}"
    
    if [ -n "$webhook_url" ] && [ "$webhook_url" != "unset" ]; then
        echo "📱 Sending system status to Slack..."
        
        # Send to Slack using webhook
        curl -X POST -H 'Content-type: application/json' \
            --data "{\"text\":\"$message\"}" \
            "$webhook_url" \
            --silent --output /dev/null --max-time 10
        
        if [ $? -eq 0 ]; then
            echo "✅ Slack notification sent successfully!"
        else
            echo "⚠️  Failed to send Slack notification (check webhook URL or network)"
        fi
    else
        echo "⚠️  No Slack webhook URL configured, skipping notification"
        echo "💡 Set SLACK_WEBHOOK_URL environment variable to enable Slack notifications"
    fi
}

# Prepare comprehensive system status message for Slack
echo ""
echo "📱 Preparing Slack notification..."

# Build status message
slack_message="🏁 *WFR DAQ System Startup Complete!*

📊 *System Status:*"

# Add service status to Slack message
for service in "${services[@]}"; do
    if [ "$service" = "startup-data-loader" ]; then
        if container_exists "$service"; then
            exit_code=$(docker inspect startup-data-loader --format='{{.State.ExitCode}}' 2>/dev/null || echo "unknown")
            if [ "$exit_code" = "0" ]; then
                slack_message="$slack_message
:white_check_mark: $service: SERVICE COMPLETE - STOPPED"
            else
                slack_message="$slack_message
:warning: $service: COMPLETED WITH EXIT CODE $exit_code"
            fi
        else
            slack_message="$slack_message
:information_source: $service: NOT NEEDED (NO DATA FILES)"
        fi
    elif container_running "$service"; then
        slack_message="$slack_message
:white_check_mark: $service: RUNNING"
    elif container_exists "$service"; then
        slack_message="$slack_message
:warning: $service: EXISTS BUT NOT RUNNING"
    else
        slack_message="$slack_message
:x: $service: NOT FOUND"
    fi
done

# Add connectivity test results
slack_message="$slack_message

:microscope: *Connectivity Tests:*"

if curl -s "http://localhost:8086/health" >/dev/null 2>&1; then
    slack_message="$slack_message
:white_check_mark: InfluxDB: Accessible"
else
    slack_message="$slack_message
:x: InfluxDB: Not accessible"
fi

if curl -s "http://localhost:8087/api/health" >/dev/null 2>&1; then
    slack_message="$slack_message
:white_check_mark: Grafana: Accessible"
else
    slack_message="$slack_message
:x: Grafana: Not accessible"
fi

if curl -s "http://localhost:8060" >/dev/null 2>&1; then
    slack_message="$slack_message
:white_check_mark: Frontend: Accessible"
else
    slack_message="$slack_message
:x: Frontend: Not accessible"
fi

# Add service URLs
slack_message="$slack_message

:globe_with_meridians: *Service URLs:*
:bar_chart: Grafana: http://127.0.0.1:8087
:file_cabinet: InfluxDB: http://127.0.0.1:8086
:desktop_computer: Frontend: http://127.0.0.1:8060
:satellite: CAN Receiver: http://127.0.0.1:8085
:chart_with_upwards_trend: Lap Timer: http://127.0.0.1:8050

:racing_car: *Ready for data acquisition!*"

# Send the comprehensive message to Slack
send_slack_notification "$slack_message"
