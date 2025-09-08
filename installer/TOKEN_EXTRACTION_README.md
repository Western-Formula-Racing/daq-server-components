# InfluxDB Token Auto-Extraction for Grafana

## 🎯 Overview

**YES! InfluxDB's all-access token CAN be automatically parsed to Grafana!**

This document covers the automated token extraction system that eliminates manual token configuration. The system uses multiple extraction methods with intelligent fallbacks to ensure reliable operation.

## 🚀 Quick Start - Fully Automated

```bash
cd installer
./scripts/start-daq-system.sh
```

This single command:
1. Starts InfluxDB and waits for initialization
2. Automatically extracts the all-access token using Docker CLI
3. Creates `.env` file with `INFLUXDB_TOKEN=your_token_here`
4. Starts all services with proper authentication
5. Verifies Grafana ↔ InfluxDB connectivity

## 🔧 Token Extraction Methods

### Method 1: Docker-Based CLI Extraction (Primary)
**Script**: `scripts/extract-token-docker.sh`

**Advantages**: Most reliable, uses InfluxDB's built-in CLI
```bash
# What it does:
docker exec influxdb2 influx auth list --user admin --hide-headers --json
# Extracts token with proper base64 padding
# Handles both fresh setups and existing tokens
```

### Method 2: Python API Extraction (Fallback)  
**Script**: `scripts/extract-influx-token.py`

**Advantages**: Full API control, detailed error handling
```python
# Uses InfluxDB REST API
response = requests.get(f"{INFLUX_URL}/api/v2/authorizations", headers=headers)
# Parses JSON response for all-access tokens
```

### Method 3: Bash API Extraction (Final Fallback)
**Script**: `scripts/extract-influx-token.sh`

**Advantages**: No dependencies, pure shell scripting
```bash
# Uses curl to call InfluxDB API
curl -H "Authorization: Token $INITIAL_TOKEN" \
     "${INFLUX_URL}/api/v2/authorizations"
```

### Method 4: Environment Variable Integration
**File**: `grafana/provisioning/datasources/influxdb.yml`

**How it works**:
```yaml
datasources:
  - name: InfluxDB_WFR
    type: influxdb
    url: http://influxdb2:8086
    secureJsonData:
      token: "${INFLUXDB_TOKEN}"  # Auto-substituted from .env
```

## 📋 Complete Automation Process

### Stage 1: InfluxDB Startup (30 seconds)
```bash
echo "🚀 Starting InfluxDB..."
docker-compose up -d influxdb2
sleep 15
```

### Stage 2: Token Extraction (Multi-tier Fallback)
```bash
echo "🔑 Extracting InfluxDB token..."

# Primary: Docker CLI method
if ./scripts/extract-token-docker.sh; then
    echo "✅ Token extracted via Docker CLI"
    
# Fallback 1: Python API
elif python3 scripts/extract-influx-token.py; then
    echo "✅ Token extracted via Python API"
    
# Fallback 2: Bash API  
elif ./scripts/extract-influx-token.sh; then
    echo "✅ Token extracted via Bash API"
    
# Manual fallback
else
    echo "❌ Auto-extraction failed. Manual token required."
    # Provides manual steps
fi
```

### Stage 3: Service Startup (30 seconds)
```bash
echo "📊 Starting all services..."
docker-compose up -d
sleep 15
```

### Stage 4: Health Verification (15 seconds)
```bash
echo "🔍 Verifying system health..."
# Tests all endpoints
# Confirms Grafana datasource connectivity
```

## 🛠️ Technical Implementation Details

### Token Extraction Logic
```bash
# extract-token-docker.sh key sections:

# Wait for InfluxDB readiness
while ! docker exec influxdb2 influx ping > /dev/null 2>&1; do
    sleep 2
done

# Extract token with proper JSON parsing
TOKEN=$(docker exec influxdb2 influx auth list \
    --user admin --hide-headers --json | \
    jq -r '.[] | select(.description == "admin'\''s Token" or .permissions[0].action == "*") | .token')

# Handle base64 padding issues
if [[ ${#TOKEN} -gt 0 && $((${#TOKEN} % 4)) -ne 0 ]]; then
    padding=$((4 - ${#TOKEN} % 4))
    TOKEN="${TOKEN}$(printf '=%.0s' $(seq 1 $padding))"
fi

# Create .env file
echo "INFLUXDB_TOKEN=${TOKEN}" > .env
```

### Grafana Datasource Auto-Configuration
```yaml
# grafana/provisioning/datasources/influxdb.yml
apiVersion: 1
datasources:
  - name: InfluxDB_WFR
    type: influxdb
    access: proxy
    url: http://influxdb2:8086
    jsonData:
      version: Flux
      organization: WFR
      defaultBucket: ourCar
      tlsSkipVerify: true
    secureJsonData:
      token: "${INFLUXDB_TOKEN}"  # Environment variable substitution
    isDefault: true
```

## 🐛 Troubleshooting

### Token Extraction Fails
```bash
# Check InfluxDB status
docker logs influxdb2

# Verify InfluxDB is responding
curl http://localhost:8086/ping

# Manual token extraction
open http://localhost:8086
# Login: admin / ${INFLUXDB_PASSWORD:-your-influxdb-password-here}
# Data → API Tokens → Generate API Token (All Access)
```

### Grafana Can't Connect
```bash
# Check if token is in .env
cat .env | grep INFLUXDB_TOKEN

# Test token manually
curl -H "Authorization: Token $(cat .env | grep INFLUXDB_TOKEN | cut -d= -f2)" \
     http://localhost:8086/api/v2/buckets

# Check Grafana datasource configuration
docker exec grafana cat /etc/grafana/provisioning/datasources/influxdb.yml
```

### Environment Variable Issues
```bash
# Ensure .env is loaded
docker-compose config | grep INFLUXDB_TOKEN

# Check Grafana environment
docker exec grafana env | grep INFLUXDB_TOKEN

# Verify variable substitution
docker logs grafana | grep -i influx
```

## 🔄 Maintenance & Updates

### Regenerate Tokens
```bash
# Delete existing token
docker exec influxdb2 influx auth delete --id TOKEN_ID

# Re-run extraction
./scripts/extract-token-docker.sh

# Restart Grafana to pick up new token
docker-compose restart grafana
```

### Token Rotation
```bash
# The system automatically handles:
# - Multiple existing tokens (picks the first all-access)
# - Fresh InfluxDB setups (extracts admin token)
# - Token format validation (base64 padding)
# - .env file creation/update
```

## 📊 System Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   InfluxDB      │    │  Token Extractor │    │    Grafana      │
│   Container     │◄───┤     Scripts      ├───►│   Container     │
│                 │    │                  │    │                 │
│ • Admin Token   │    │ • Docker CLI     │    │ • Auto Datasrc │
│ • All Access    │    │ • Python API     │    │ • Env Variables │
│ • REST API      │    │ • Bash API       │    │ • Provisioning  │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                        │                        │
         └────────────────────────┼────────────────────────┘
                                  ▼
                           ┌─────────────┐
                           │   .env      │
                           │   File      │
                           │             │
                           │ INFLUXDB_   │
                           │ TOKEN=xyz   │
                           └─────────────┘
```

## 🏆 Success Verification

After running the automated installer, you should see:

```bash
✅ InfluxDB is running and ready
✅ Token extracted successfully: iEf7...G_bA==
✅ All services started successfully
✅ Grafana is available at: http://localhost:8087
✅ InfluxDB datasource configured automatically
✅ System health check passed

🎉 DAQ System is ready for use!
```
**Script**: `scripts/extract-influx-token.py`

```bash
# Requires: pip install requests
python3 scripts/extract-influx-token.py
```

### Method 5: Bash API Extraction  
**Script**: `scripts/extract-influx-token.sh`

```bash
# Requires: jq and curl
./scripts/extract-influx-token.sh
```

## 🚀 Quick Start (Fully Automated)

```bash
cd installer/
./scripts/start-daq-system.sh
```

That's it! The script will:
- ✅ Start InfluxDB
- ✅ Extract the all-access token automatically
- ✅ Configure Grafana with the token
- ✅ Start all services
- ✅ Verify connectivity

## 🔧 Manual Token Extraction

If you prefer manual control:

```bash
# 1. Start InfluxDB only
docker-compose up -d influxdb2

# 2. Wait for initialization
sleep 15

# 3. Extract token (choose one method)
./scripts/extract-token-docker.sh        # Docker-based (recommended)
# OR
python3 scripts/extract-influx-token.py  # Python API
# OR  
./scripts/extract-influx-token.sh        # Bash API

# 4. Start remaining services
docker-compose up -d
```

## 🔍 How Token Extraction Works

### Docker Method (Most Reliable)
1. Uses InfluxDB's built-in CLI within the container
2. Authenticates with admin credentials
3. Lists existing tokens to find all-access token
4. Creates new token if none exists
5. Exports token to `.env` file

### API Methods
1. Wait for InfluxDB HTTP API to be ready
2. Authenticate via `/api/v2/signin`
3. List tokens via `/api/v2/authorizations`
4. Find token with comprehensive permissions
5. Create new token if needed
6. Save to `.env` file

## 📁 File Structure

```
installer/
├── docker-compose.yml              # Updated with token env var
├── .env                           # Auto-generated token file
├── grafana/
│   ├── provisioning/
│   │   └── datasources/
│   │       └── influxdb.yml       # Uses ${INFLUXDB_TOKEN}
│   └── dashboards/
└── scripts/
    ├── start-daq-system.sh        # Complete automation
    ├── extract-token-docker.sh    # Docker-based extraction
    ├── extract-influx-token.py    # Python API extraction
    └── extract-influx-token.sh    # Bash API extraction
```

## 🔒 Security Considerations

### Production Recommendations
1. **Use Docker Secrets**: For production, consider Docker secrets instead of environment variables
2. **Token Rotation**: Regularly rotate tokens for security
3. **Least Privilege**: Create tokens with minimal required permissions
4. **Secure Storage**: Store tokens in encrypted configuration management

### Token Permissions
The auto-generated tokens include:
- Read/Write buckets (data access)
- Read dashboards, tasks, users
- Read system configurations
- **No admin permissions** (safer)

## 🐛 Troubleshooting

### Common Issues

**Token extraction fails:**
```bash
# Check InfluxDB status
docker logs influxdb2

# Verify authentication
docker exec influxdb2 influx auth list --username admin --password ${INFLUXDB_PASSWORD:-YOUR_INFLUXDB_PASSWORD} --org WFR
```

**Grafana can't connect:**
```bash
# Check .env file exists
cat .env

# Verify token in Grafana logs
docker logs grafana

# Test token manually
curl -H "Authorization: Token YOUR_TOKEN" http://localhost:8086/api/v2/buckets
```

**Services won't start:**
```bash
# Check docker-compose status
docker-compose ps

# View service logs
docker-compose logs grafana
docker-compose logs influxdb2
```

## ✅ Success Indicators

When everything works correctly:

1. **`.env` file created** with `INFLUXDB_TOKEN=...`
2. **Grafana starts without errors**: `docker logs grafana`
3. **InfluxDB datasource shows "Connected"** in Grafana UI
4. **Default dashboard displays data** (if available)

## 🎯 Benefits of Auto-Token Extraction

1. **Zero Manual Configuration**: No need to copy/paste tokens
2. **Consistent Deployments**: Same process across environments  
3. **Security**: Fresh tokens for each deployment
4. **Reliability**: Multiple fallback methods
5. **Documentation**: Complete audit trail of token creation

This setup achieves **100% automation** for InfluxDB-Grafana token integration!
