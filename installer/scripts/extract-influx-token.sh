#!/bin/bash
set -e

CONTAINER_NAME="influxdb2"
INFLUXDB_USERNAME="admin"
INFLUXDB_PASSWORD="${INFLUXDB_PASSWORD:-YOUR_INFLUXDB_PASSWORD}"
INFLUXDB_ORG="WFR"

echo "🔍 Docker-based InfluxDB Token Extraction"
echo "========================================"

if ! docker ps | grep -q "$CONTAINER_NAME"; then
    echo "❌ InfluxDB container '$CONTAINER_NAME' is not running."
    exit 1
fi
echo "✅ InfluxDB container is running."

# Wait for InfluxDB to be ready
echo "⏳ Waiting for InfluxDB to be ready..."
for i in {1..30}; do
    if docker exec "$CONTAINER_NAME" influx ping >/dev/null 2>&1; then
        echo "✅ InfluxDB is ready!"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "❌ InfluxDB failed to become ready."
        exit 1
    fi
    sleep 1
done

# Configure the influx CLI to use the correct credentials
docker exec "$CONTAINER_NAME" influx config create \
    --config-name default \
    --host-url http://localhost:8086 \
    --org "$INFLUXDB_ORG" \
    --username-password "$INFLUXDB_USERNAME:$INFLUXDB_PASSWORD" \
    --active >/dev/null 2>&1 || echo "ℹ️ CLI config already exists."

# Find the Operator's token for the 'admin' user
echo "📋 Searching for existing operator token..."
TOKEN_OUTPUT=$(docker exec "$CONTAINER_NAME" influx auth list --user "$INFLUXDB_USERNAME" --json 2>/dev/null)
ALL_ACCESS_TOKEN=$(echo "$TOKEN_OUTPUT" | jq -r 'map(select(.description | contains("admin")))[0].token')

if [ -z "$ALL_ACCESS_TOKEN" ] || [ "$ALL_ACCESS_TOKEN" = "null" ]; then
    echo "❌ Could not find the Operator's token for user '$INFLUXDB_USERNAME'."
    echo "💡 Try resetting InfluxDB with 'docker-compose down -v' and re-running."
    exit 1
fi

echo "✅ Found existing all-access token!"
echo "🔑 Token extracted: ${ALL_ACCESS_TOKEN:0:20}..."

# Write or update token in .env file
echo "💾 Writing token to .env file..."
if grep -q "^INFLUXDB_TOKEN=" .env 2>/dev/null; then
    sed -i.bak "s/^INFLUXDB_TOKEN=.*/INFLUXDB_TOKEN=$ALL_ACCESS_TOKEN/" .env
else
    echo "INFLUXDB_TOKEN=$ALL_ACCESS_TOKEN" >> .env
fi
echo "✅ Token updated in .env file!"
