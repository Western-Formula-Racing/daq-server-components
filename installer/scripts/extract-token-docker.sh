#!/bin/bash

# Docker-based InfluxDB Token Extractor
# Uses InfluxDB CLI within the container to extract tokens

set -e

CONTAINER_NAME="influxdb2"
INFLUXDB_USERNAME="admin"
INFLUXDB_PASSWORD="${INFLUXDB_PASSWORD:-YOUR_INFLUXDB_PASSWORD}"
INFLUXDB_ORG="WFR"

echo "🔍 Docker-based InfluxDB Token Extraction"
echo "========================================"

# Check if InfluxDB container is running
if ! docker ps | grep -q "$CONTAINER_NAME"; then
    echo "❌ InfluxDB container '$CONTAINER_NAME' is not running"
    echo "💡 Start it first with: docker-compose up -d influxdb2"
    exit 1
fi

echo "✅ InfluxDB container is running"

# Wait for InfluxDB to be ready with better error handling
echo "⏳ Waiting for InfluxDB to be ready..."
for i in {1..60}; do
    if docker exec "$CONTAINER_NAME" influx ping >/dev/null 2>&1; then
        echo "✅ InfluxDB is ready!"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "❌ InfluxDB failed to become ready after 60 attempts"
        echo "🔍 Check logs: docker logs influxdb2"
        exit 1
    fi
    echo "   Attempt $i/60: InfluxDB not ready yet, waiting 1 second..."
    sleep 1
done

# First, let's try to use influx setup if not already done
echo "� Checking InfluxDB setup status..."

# Try to get an initial token from setup (this only works if setup hasn't been run)
INITIAL_SETUP=$(docker exec "$CONTAINER_NAME" influx setup \
    --username "$INFLUXDB_USERNAME" \
    --password "$INFLUXDB_PASSWORD" \
    --org "$INFLUXDB_ORG" \
    --bucket ourCar \
    --force 2>/dev/null || echo "already_setup")

if [ "$INITIAL_SETUP" != "already_setup" ]; then
    echo "✅ InfluxDB initial setup completed!"
    # Extract token from setup output
    SETUP_TOKEN=$(echo "$INITIAL_SETUP" | grep -o 'User:\|.*admin.*' | tail -1 | grep -o '[a-zA-Z0-9_-]\{64,\}' || echo "")
    if [ -n "$SETUP_TOKEN" ]; then
        ALL_ACCESS_TOKEN="$SETUP_TOKEN"
        echo "✅ Got token from initial setup!"
    fi
else
    echo "ℹ️  InfluxDB already configured, looking for existing tokens..."
fi

# If we don't have a token yet, try to list existing ones
if [ -z "$ALL_ACCESS_TOKEN" ]; then
    echo "📋 Searching for existing all-access tokens..."
    
    # Try using config-based authentication
    docker exec "$CONTAINER_NAME" influx config create \
        --config-name default \
        --host-url http://localhost:8086 \
        --org "$INFLUXDB_ORG" \
        --username-password "$INFLUXDB_USERNAME:$INFLUXDB_PASSWORD" \
        --active >/dev/null 2>&1 || echo "Config already exists"
    
    # List tokens in table format and extract the longest token (likely all-access)
    TOKEN_OUTPUT=$(docker exec "$CONTAINER_NAME" influx auth list 2>/dev/null || echo "")
    
    # Extract the longest token from the output (all-access tokens are typically longer)
    # Include potential == padding at the end
    ALL_ACCESS_TOKEN=$(echo "$TOKEN_OUTPUT" | grep -oE '[A-Za-z0-9_-]{64,}(==)?' | head -1 || echo "")
fi

# If we still don't have a token, create a new one
if [ -z "$ALL_ACCESS_TOKEN" ]; then
    echo "⚠️  No suitable existing token found, creating new all-access token..."
    
    # Create a new all-access token using the active config
    CREATE_OUTPUT=$(docker exec "$CONTAINER_NAME" influx auth create \
        --description "Grafana All-Access Token - $(date)" \
        --read-buckets \
        --write-buckets \
        --read-dashboards \
        --read-tasks \
        --read-telegrafs \
        --read-users \
        --read-variables \
        --read-scrapers \
        --read-secrets \
        --read-labels \
        --read-views \
        --read-documents \
        --read-notificationRules \
        --read-notificationEndpoints \
        --read-checks \
        --read-dbrp 2>/dev/null || echo "failed")
    
    # Extract token from the output (it should be the last field in the table)
    ALL_ACCESS_TOKEN=$(echo "$CREATE_OUTPUT" | tail -1 | awk '{print $NF}' | grep -E '^[A-Za-z0-9_-]{64,}(==)?$' || echo "")
    
    if [ -z "$ALL_ACCESS_TOKEN" ]; then
        echo "❌ Failed to create all-access token"
        echo "🔍 Try manual token creation in InfluxDB UI: http://localhost:8086"
        echo "🔍 Create output was: $CREATE_OUTPUT"
        exit 1
    fi
    
    echo "✅ Created new all-access token!"
else
    echo "✅ Found existing all-access token!"
fi

echo "🔑 Token extracted: ${ALL_ACCESS_TOKEN:0:20}..."

# Write token to .env file
echo "💾 Writing token to .env file..."
cat > .env << EOF
# InfluxDB Configuration
INFLUXDB_TOKEN=$ALL_ACCESS_TOKEN

# Generated on: $(date)
# This token provides all-access permissions for Grafana integration
# Extracted using Docker-based method
EOF

echo "✅ Token saved to .env file!"
echo ""
echo "🚀 You can now start/restart the complete stack with:"
echo "   docker-compose up -d"
echo ""
echo "📊 Grafana will automatically use this token to connect to InfluxDB!"
