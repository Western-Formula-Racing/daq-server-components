#!/bin/bash

# InfluxDB Token Extractor for Grafana Auto-Configuration
# This script extracts the all-access token from InfluxDB and sets it for Grafana

set -e

INFLUXDB_URL="http://localhost:8086"
INFLUXDB_USERNAME="admin"
INFLUXDB_PASSWORD="YOUR_INFLUXDB_PASSWORD"
INFLUXDB_ORG="WFR"

echo "🔍 Extracting InfluxDB All-Access Token..."

# Wait for InfluxDB to be ready
echo "⏳ Waiting for InfluxDB to be ready..."
for i in {1..30}; do
    if curl -s "${INFLUXDB_URL}/health" >/dev/null 2>&1; then
        echo "✅ InfluxDB is ready!"
        break
    fi
    echo "   Attempt $i/30: InfluxDB not ready yet, waiting 2 seconds..."
    sleep 2
done

# Get authentication token first
echo "🔐 Getting authentication token..."
AUTH_RESPONSE=$(curl -s -X POST "${INFLUXDB_URL}/api/v2/signin" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"${INFLUXDB_USERNAME}\",\"password\":\"${INFLUXDB_PASSWORD}\"}" \
    --cookie-jar /tmp/influx_cookies.txt)

if [ $? -ne 0 ]; then
    echo "❌ Failed to authenticate with InfluxDB"
    exit 1
fi

echo "🎫 Authentication successful!"

# List all tokens to find the all-access token
echo "📋 Fetching all available tokens..."
TOKENS_RESPONSE=$(curl -s -X GET "${INFLUXDB_URL}/api/v2/authorizations" \
    -H "Content-Type: application/json" \
    --cookie /tmp/influx_cookies.txt)

if [ $? -ne 0 ]; then
    echo "❌ Failed to fetch tokens from InfluxDB"
    exit 1
fi

# Extract the all-access token (usually the first one or the one with most permissions)
ALL_ACCESS_TOKEN=$(echo "$TOKENS_RESPONSE" | jq -r '.authorizations[] | select(.permissions | length > 10) | .token' | head -1)

if [ -z "$ALL_ACCESS_TOKEN" ] || [ "$ALL_ACCESS_TOKEN" = "null" ]; then
    echo "⚠️  No all-access token found, creating one..."
    
    # Create a new all-access token
    CREATE_TOKEN_RESPONSE=$(curl -s -X POST "${INFLUXDB_URL}/api/v2/authorizations" \
        -H "Content-Type: application/json" \
        --cookie /tmp/influx_cookies.txt \
        -d "{
            \"description\": \"Grafana All-Access Token - $(date)\",
            \"orgID\": \"$(echo "$TOKENS_RESPONSE" | jq -r '.authorizations[0].orgID')\",
            \"permissions\": [
                {\"action\": \"read\", \"resource\": {\"type\": \"buckets\"}},
                {\"action\": \"write\", \"resource\": {\"type\": \"buckets\"}},
                {\"action\": \"read\", \"resource\": {\"type\": \"dashboards\"}},
                {\"action\": \"read\", \"resource\": {\"type\": \"tasks\"}},
                {\"action\": \"read\", \"resource\": {\"type\": \"telegrafs\"}},
                {\"action\": \"read\", \"resource\": {\"type\": \"users\"}},
                {\"action\": \"read\", \"resource\": {\"type\": \"variables\"}},
                {\"action\": \"read\", \"resource\": {\"type\": \"scrapers\"}},
                {\"action\": \"read\", \"resource\": {\"type\": \"secrets\"}},
                {\"action\": \"read\", \"resource\": {\"type\": \"labels\"}},
                {\"action\": \"read\", \"resource\": {\"type\": \"views\"}},
                {\"action\": \"read\", \"resource\": {\"type\": \"documents\"}},
                {\"action\": \"read\", \"resource\": {\"type\": \"notificationRules\"}},
                {\"action\": \"read\", \"resource\": {\"type\": \"notificationEndpoints\"}},
                {\"action\": \"read\", \"resource\": {\"type\": \"checks\"}},
                {\"action\": \"read\", \"resource\": {\"type\": \"dbrp\"}}
            ]
        }")
    
    ALL_ACCESS_TOKEN=$(echo "$CREATE_TOKEN_RESPONSE" | jq -r '.token')
    
    if [ -z "$ALL_ACCESS_TOKEN" ] || [ "$ALL_ACCESS_TOKEN" = "null" ]; then
        echo "❌ Failed to create all-access token"
        exit 1
    fi
    
    echo "✅ Created new all-access token!"
else
    echo "✅ Found existing all-access token!"
fi

# Clean up cookies
rm -f /tmp/influx_cookies.txt

echo "🔑 Token extracted: ${ALL_ACCESS_TOKEN:0:20}..."

# Export the token as environment variable
export INFLUXDB_TOKEN="$ALL_ACCESS_TOKEN"

# Write token to .env file for docker-compose
echo "💾 Writing token to .env file..."
cat > .env << EOF
# InfluxDB Configuration
INFLUXDB_TOKEN=$ALL_ACCESS_TOKEN

# Generated on: $(date)
# This token provides all-access permissions for Grafana integration
EOF

echo "✅ Token saved to .env file!"
echo ""
echo "🚀 You can now start the stack with:"
echo "   docker-compose up -d"
echo ""
echo "📊 Grafana will automatically use this token to connect to InfluxDB!"
