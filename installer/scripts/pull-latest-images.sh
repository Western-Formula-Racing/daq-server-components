#!/bin/bash

# WFR DAQ System - Pull Latest Images from GitHub Container Registry
# This script pulls the latest built images from GitHub Actions CI/CD

set -e

echo "🐳 Pulling latest WFR DAQ System images from GitHub Container Registry..."
echo "======================================================================"

# Define the registry and repository
REGISTRY="ghcr.io"
REPO="western-formula-racing/daq-server-components"

# List of custom services to pull
SERVICES=(
    "car-to-influx"
    "slackbot"
    "lappy"
    "startup-data-loader"
    "file-uploader"
)

# Pull each service image
for service in "${SERVICES[@]}"; do
    IMAGE="${REGISTRY}/${REPO}/${service}:latest"
    echo "📦 Pulling ${service}..."
    if docker pull "$IMAGE"; then
        echo "✅ Successfully pulled ${service}"
    else
        echo "❌ Failed to pull ${service}"
        exit 1
    fi
done

# Also pull base images (these are usually already available)
echo ""
echo "📦 Pulling base images..."
docker pull influxdb:2
docker pull grafana/grafana
docker pull nginx:alpine

echo ""
echo "✅ All images pulled successfully!"
echo ""
echo "🚀 You can now run: docker-compose up -d"
echo ""
echo "📊 To see all images: docker images | grep ${REPO}"
