# Quick Start - Preset Token Setup

## TL;DR - Get Running in 2 Minutes

### 1. Setup Environment (30 seconds)
```bash
cd installer
cp .env.example .env
```

**Optional but Recommended:** Generate a secure token
```bash
# Generate secure token
openssl rand -base64 32

# Or use Python
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Edit `.env` and set:
```bash
INFLUXDB_ADMIN_TOKEN=your-generated-token-here
```

### 2. Start Everything (30 seconds)
```bash
docker-compose up -d
```

### 3. Done! (30 seconds for services to start)
Access your services:
- **Grafana**: http://localhost:8087 (admin/admin)
- **InfluxDB**: http://localhost:8086 (admin/your-password)
- **Frontend**: http://localhost:8060
- **CAN Receiver**: http://localhost:8085
- **File Uploader**: http://localhost:8084
- **Lap Timer**: http://localhost:8050

---

## What Changed?

### Old Way (Complex) ❌
```bash
docker-compose up -d influxdb2        # Start DB only
sleep 30                               # Wait
./scripts/extract-influx-token.sh     # Extract token
# Handle potential failures
docker-compose up -d                   # Start rest
```

### New Way (Simple) ✅
```bash
docker-compose up -d                   # Start everything
```

---

## Key Points

### Environment Variable
**All services now use:** `INFLUXDB_ADMIN_TOKEN`

**Default value if not set:** `wfr-admin-token-change-in-production`

### Security
⚠️ **Change the default token in production!**

```bash
# In .env file:
INFLUXDB_ADMIN_TOKEN=your-super-secret-token-here
```

### Services Using Token
- ✅ InfluxDB (initialization)
- ✅ Grafana (datasource)
- ✅ car-to-influx (CAN data)
- ✅ file-uploader
- ✅ startup-data-loader
- ✅ influxdb3

---

## Troubleshooting

### Problem: Services can't connect to InfluxDB
```bash
# Check token in .env
grep INFLUXDB_ADMIN_TOKEN .env

# Restart services
docker-compose restart
```

### Problem: Need fresh start
```bash
# Complete reset
docker-compose down -v
docker-compose up -d
```

### Problem: Check if working
```bash
# See all running services
docker ps

# Check logs
docker-compose logs -f

# Test InfluxDB
curl http://localhost:8086/health

# Test Grafana  
curl http://localhost:8087/api/health
```

---

## Advanced

### Custom Token per Service
Edit `docker-compose.yml` if you need different tokens:

```yaml
environment:
  INFLUXDB_TOKEN: "${READ_ONLY_TOKEN}"  # For read-only services
```

### Using with CI/CD
```bash
# Set token via environment variable
export INFLUXDB_ADMIN_TOKEN="your-ci-token"
docker-compose up -d
```

### Docker Secrets (Production)
```yaml
secrets:
  influx_token:
    external: true
    
services:
  car-to-influx:
    secrets:
      - influx_token
    environment:
      INFLUXDB_TOKEN_FILE: /run/secrets/influx_token
```

---

## Files Reference

- 📄 `docker-compose.yml` - Main configuration (updated with preset token)
- 📄 `.env.example` - Template with INFLUXDB_ADMIN_TOKEN
- 📄 `SIMPLIFIED_SETUP.md` - Full documentation
- 📄 `MIGRATION_SUMMARY.md` - Complete change log
- 🔧 `scripts/migrate-to-preset-token.sh` - Migration helper
- 🔧 `scripts/start-daq-system.sh` - Simplified startup (no token extraction)

---

## Need Help?

1. **Check logs**: `docker-compose logs -f`
2. **Validate config**: `docker-compose config`
3. **See running services**: `docker ps`
4. **Read full docs**: `SIMPLIFIED_SETUP.md`
5. **Migration guide**: `MIGRATION_SUMMARY.md`

---

**That's it! No more shell scripts, no more token extraction, just pure Docker Compose goodness! 🚀**
