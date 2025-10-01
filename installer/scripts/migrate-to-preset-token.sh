#!/bin/bash

# Migration Script: From Token Extraction to Preset Token Setup
# This script helps you migrate from the old token extraction method to the new preset token approach

set -e

echo "🔄 DAQ System Migration: Token Extraction → Preset Token"
echo "=========================================================="
echo ""

# Check if we're in the right directory
if [ ! -f "docker-compose.yml" ]; then
    echo "❌ ERROR: docker-compose.yml not found. Please run this script from the installer directory."
    exit 1
fi

echo "📋 Pre-Migration Checklist:"
echo "1. This will stop all running containers"
echo "2. You can optionally backup your InfluxDB data"
echo "3. A new preset token will be configured"
echo ""
read -p "Do you want to proceed? (y/n) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Migration cancelled."
    exit 0
fi

# Backup option
echo ""
read -p "Do you want to backup InfluxDB data before migration? (y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "📦 Creating backup..."
    
    # Check if InfluxDB is running
    if docker ps | grep -q influxdb2; then
        # Get current token if available
        if [ -f .env ] && grep -q "INFLUXDB_TOKEN" .env; then
            CURRENT_TOKEN=$(grep INFLUXDB_TOKEN .env | cut -d= -f2)
            echo "Found existing token, creating backup..."
            
            docker exec influxdb2 influx backup /tmp/backup -t "$CURRENT_TOKEN" 2>/dev/null || {
                echo "⚠️  Backup failed. Continuing without backup..."
            }
            
            if [ $? -eq 0 ]; then
                docker cp influxdb2:/tmp/backup ./influx-backup-$(date +%Y%m%d-%H%M%S)
                echo "✅ Backup created in ./influx-backup-$(date +%Y%m%d-%H%M%S)"
            fi
        else
            echo "⚠️  No existing token found, skipping backup."
        fi
    else
        echo "⚠️  InfluxDB not running, skipping backup."
    fi
fi

# Generate a secure token
echo ""
echo "🔐 Generating secure admin token..."
NEW_TOKEN=$(openssl rand -base64 32 | tr -d '\n' | tr '+/' '-_')
echo "Generated token: ${NEW_TOKEN:0:20}..."

# Update .env file
echo ""
echo "📝 Updating .env file..."

if [ -f .env ]; then
    # Backup current .env
    cp .env .env.backup-$(date +%Y%m%d-%H%M%S)
    echo "✅ Backed up existing .env file"
    
    # Remove old INFLUXDB_TOKEN line if exists
    sed -i.tmp '/^INFLUXDB_TOKEN=/d' .env
    rm -f .env.tmp
    
    # Add new INFLUXDB_ADMIN_TOKEN
    if grep -q "^INFLUXDB_ADMIN_TOKEN=" .env; then
        # Update existing line
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|^INFLUXDB_ADMIN_TOKEN=.*|INFLUXDB_ADMIN_TOKEN=$NEW_TOKEN|" .env
        else
            sed -i "s|^INFLUXDB_ADMIN_TOKEN=.*|INFLUXDB_ADMIN_TOKEN=$NEW_TOKEN|" .env
        fi
    else
        # Add new line
        echo "" >> .env
        echo "# InfluxDB Admin Token (preset for all services to use)" >> .env
        echo "INFLUXDB_ADMIN_TOKEN=$NEW_TOKEN" >> .env
    fi
    
    echo "✅ Updated .env file with new admin token"
else
    echo "⚠️  No .env file found. Creating from .env.example..."
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "INFLUXDB_ADMIN_TOKEN=$NEW_TOKEN" >> .env
        echo "✅ Created .env file with admin token"
    else
        echo "❌ ERROR: Neither .env nor .env.example found!"
        exit 1
    fi
fi

# Stop all services
echo ""
echo "🛑 Stopping all services..."
docker-compose down

# Ask about volume removal
echo ""
echo "⚠️  To complete the migration, InfluxDB volumes should be removed."
echo "This will delete all existing data (unless you created a backup)."
read -p "Remove InfluxDB volumes? (y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    docker-compose down -v
    echo "✅ Volumes removed"
else
    echo "⚠️  Volumes kept. Note: The new token may not work with existing data."
fi

# Start services
echo ""
echo "🚀 Starting services with new configuration..."
docker-compose up -d

echo ""
echo "⏳ Waiting for services to start..."
sleep 15

# Check service health
echo ""
echo "🔍 Checking service health..."
if curl -s "http://localhost:8086/health" >/dev/null 2>&1; then
    echo "✅ InfluxDB: Running"
else
    echo "⚠️  InfluxDB: Not responding yet (may need more time)"
fi

if curl -s "http://localhost:8087/api/health" >/dev/null 2>&1; then
    echo "✅ Grafana: Running"
else
    echo "⚠️  Grafana: Not responding yet (may need more time)"
fi

# Summary
echo ""
echo "✅ Migration Complete!"
echo "====================="
echo ""
echo "📝 Summary:"
echo "  • Old token extraction removed"
echo "  • New preset token configured"
echo "  • All services restarted"
echo ""
echo "🔑 Your new admin token:"
echo "  INFLUXDB_ADMIN_TOKEN=$NEW_TOKEN"
echo ""
echo "🌐 Service URLs:"
echo "  • InfluxDB: http://localhost:8086"
echo "  • Grafana: http://localhost:8087"
echo "  • Frontend: http://localhost:8060"
echo ""
echo "📋 Next Steps:"
echo "  1. Verify all services are running: docker ps"
echo "  2. Check logs if needed: docker-compose logs -f"
echo "  3. Access Grafana and verify InfluxDB connection"
echo "  4. Test data ingestion endpoints"
echo ""
echo "💾 Backup files created:"
echo "  • .env backup: .env.backup-*"
if [ -d "influx-backup-"* ]; then
    echo "  • InfluxDB backup: influx-backup-*"
fi
echo ""

