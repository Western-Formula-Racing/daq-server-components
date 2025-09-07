# Grafana Configuration - WFR DAQ System

## 🎯 Auto-Configuration Overview

The Grafana service is fully pre-configured with automated InfluxDB integration. No manual setup required!

### 🔐 Default Credentials
- **Username**: `admin`
- **Password**: `turbo-charged-plotting-machine`
- **Email**: `daq@westernformularacing.com`
- **Access URL**: http://localhost:8087

### 🗄️ InfluxDB v2 Datasource (Auto-Configured)
- **Name**: InfluxDB_WFR
- **URL**: http://influxdb2:8086
- **Organization**: WFR
- **Default Bucket**: ourCar
- **Token**: Automatically extracted via `${INFLUXDB_TOKEN}`
- **Status**: Pre-configured and set as default
- **Query Language**: Flux

### 📊 Dashboard Provisioning
- **Provider**: Local dashboard folder
- **Source**: `grafana/dashboards/` directory
- **Auto-Import**: All JSON files automatically loaded
- **Folder**: "WFR DAQ Dashboards"

## 🏗️ Configuration Structure

```
grafana/
├── README.md                    # This file
├── DASHBOARD_IMPORT_GUIDE.md    # Manual dashboard instructions
│
├── provisioning/                # Auto-configuration files
│   ├── datasources/
│   │   └── influxdb.yml        # InfluxDB connection config
│   └── dashboards/
│       └── dashboard-provider.yml # Dashboard auto-import
│
└── dashboards/                  # Dashboard JSON files
    ├── system-overview.json     # (Example dashboard)
    └── telemetry-live.json      # (Example real-time dashboard)
```

## ⚡ Automatic Features

### ✅ What Gets Auto-Configured

1. **InfluxDB Connection**
   - Token authentication from environment variables
   - Network connectivity via Docker `datalink` network
   - Flux query language configuration
   - Default bucket and organization settings

2. **Dashboard Provisioning**
   - Automatic import of all JSON files in `dashboards/`
   - Dashboard updates on container restart
   - Folder organization for WFR dashboards

3. **User Authentication**
   - Admin account pre-created
   - Password and email pre-configured
   - Anonymous access disabled for security

4. **System Settings**
   - Time zone: UTC (optimal for race data)
   - Refresh intervals: 5s, 10s, 30s, 1m, 5m, 15m
   - Panel options optimized for telemetry data

### 🔄 Environment Integration

**Token Management**:
```yaml
# grafana/provisioning/datasources/influxdb.yml
secureJsonData:
  token: "${INFLUXDB_TOKEN}"  # Auto-populated from .env
```

**Dashboard Auto-Load**:
```yaml
# grafana/provisioning/dashboards/dashboard-provider.yml
providers:
  - name: 'WFR Dashboards'
    folder: 'WFR DAQ Dashboards'
    path: /etc/grafana/dashboards
    options:
      path: /etc/grafana/dashboards
```

## 🚀 First Login Process

1. **Access Grafana**: http://localhost:8087
2. **Login**: Use credentials above
3. **Verify Datasource**: Settings → Data Sources → InfluxDB_WFR (should show green checkmark)
4. **View Dashboards**: Dashboards → Browse → WFR DAQ Dashboards folder

## 📊 Creating Your First Dashboard

### Quick Start Query
```flux
from(bucket: "ourCar")
  |> range(start: -1h)
  |> filter(fn: (r) => r["_measurement"] == "telemetry")
  |> filter(fn: (r) => r["_field"] == "speed")
  |> aggregateWindow(every: 1s, fn: mean, createEmpty: false)
```

### Common Telemetry Queries

**Engine RPM**:
```flux
from(bucket: "ourCar")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "engine")
  |> filter(fn: (r) => r["_field"] == "rpm")
```

**Lap Times**:
```flux
from(bucket: "ourCar")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "timing")
  |> filter(fn: (r) => r["_field"] == "lap_time")
```

**Temperature Monitoring**:
```flux
from(bucket: "ourCar")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "sensors")
  |> filter(fn: (r) => r["_field"] =~ /temp/)
```

## 🎨 Dashboard Development

### Adding New Dashboards

**Method 1: File-Based (Recommended)**
1. Create JSON dashboard file
2. Save to `grafana/dashboards/your-dashboard.json`
3. Restart Grafana: `docker-compose restart grafana`
4. Dashboard auto-appears in "WFR DAQ Dashboards" folder

**Method 2: Web Interface**
1. Create dashboard in Grafana UI
2. Export as JSON
3. Save to `dashboards/` folder for persistence

### Dashboard Best Practices

**Panel Configuration**:
- Use time-series panels for telemetry data
- Set appropriate refresh intervals (5-10 seconds for live data)
- Configure proper units (km/h, RPM, °C, etc.)
- Use color coding for status indicators

**Query Optimization**:
- Use `aggregateWindow()` for performance
- Filter data early in the query pipeline
- Use variables for dynamic dashboards
- Implement proper time range handling

## 🔧 Troubleshooting

### Common Issues

**Datasource Connection Failed**:
```bash
# Check token in .env file
cat .env | grep INFLUXDB_TOKEN

# Verify InfluxDB is accessible
docker exec grafana ping influxdb2

# Check Grafana logs
docker logs grafana | grep -i influx
```

**Dashboards Not Loading**:
```bash
# Verify dashboard files exist
ls -la grafana/dashboards/

# Check provisioning configuration
docker exec grafana cat /etc/grafana/provisioning/dashboards/dashboard-provider.yml

# Restart Grafana
docker-compose restart grafana
```

**Authentication Issues**:
```bash
# Reset admin password
docker exec grafana grafana-cli admin reset-admin-password new-password

# Check user configuration
docker logs grafana | grep -i admin
```

### Manual Configuration Fallback

If auto-configuration fails:

1. **Manual Datasource Setup**:
   - Go to Settings → Data Sources → Add data source
   - Select InfluxDB
   - URL: `http://influxdb2:8086`
   - Token: Copy from `.env` file
   - Organization: `WFR`
   - Default Bucket: `ourCar`

2. **Manual Dashboard Import**:
   - Go to Dashboards → Import
   - Upload JSON files from `dashboards/` folder
   - Configure datasource as InfluxDB_WFR

## 📈 Performance Optimization

### Query Performance
- Use `|> yield()` at the end of complex queries
- Implement proper time range filters
- Use `|> limit(n: 1000)` for testing large datasets
- Cache frequently used queries with variables

### Dashboard Performance
- Limit number of panels per dashboard (max 20-30)
- Use appropriate refresh intervals
- Implement conditional queries based on time range
- Use stat panels instead of tables when possible

## 🔄 Maintenance

### Regular Tasks
```bash
# Update dashboards from Git
git pull origin main
docker-compose restart grafana

# Backup current dashboards
cp -r grafana/dashboards grafana/dashboards-backup-$(date +%Y%m%d)

# Monitor Grafana performance
docker logs grafana | tail -50
docker exec grafana ps aux
```

### Dashboard Version Control
```bash
# Export modified dashboards
# Use Grafana UI → Dashboard Settings → JSON Model
# Save to dashboards/ folder
# Commit to Git for team sharing
```

## 🏆 Success Criteria

After setup, you should have:

- ✅ Grafana accessible at http://localhost:8087
- ✅ InfluxDB_WFR datasource with green status
- ✅ All dashboards in "WFR DAQ Dashboards" folder
- ✅ Real-time data visualization working
- ✅ No authentication or connection errors
- ✅ Responsive dashboard performance

## 📞 Support

For Grafana-specific issues:
- Check `/var/log/grafana/grafana.log` in container
- Review Grafana documentation: https://grafana.com/docs/
- Test queries in InfluxDB interface first
- Contact DAQ team for dashboard development
✅ **Default admin user** - Pre-created with your credentials  
✅ **Sample dashboard** - Ready-to-use DAQ monitoring
✅ **Security settings** - Anonymous access disabled
✅ **Essential plugins** - Clock panel and JSON datasource

## What Requires Manual Configuration

❌ **Additional user accounts** - Must be created through UI
❌ **Alert rules** - Need to be configured manually
❌ **SMTP settings** - Email notifications require setup
❌ **LDAP/OAuth integration** - Enterprise features need manual config
❌ **Custom themes** - UI customization manual only

## Deployment Steps

1. **Start the stack**: `docker-compose up -d`
2. **Wait 30 seconds** for InfluxDB to fully initialize
3. **Access Grafana**: http://3.98.181.12:8087
4. **Login** with the pre-configured credentials
5. **Verify** the InfluxDB data source is connected
6. **View** the default dashboard

## Limitations

- **Token Security**: The InfluxDB token is embedded in the config file. For production, consider using Docker secrets
- **User Management**: Additional users must be added through the Grafana UI
- **Backup Configuration**: Dashboard and config backups need separate setup
- **SSL/TLS**: HTTPS setup requires additional configuration

## File Structure
```
installer/grafana/
├── provisioning/
│   ├── datasources/
│   │   └── influxdb.yml          # InfluxDB connection config
│   └── dashboards/
│       └── dashboard.yml         # Dashboard provider config
└── dashboards/
    └── wfr-daq-overview.json     # Default dashboard definition
```

This setup provides approximately **80% automation** for Grafana configuration, with the remaining 20% requiring manual setup through the web interface.
