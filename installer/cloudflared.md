# Create DNS routes
cloudflared tunnel route dns wfr-tunnel explore.0001200.xyz
cloudflared tunnel route dns wfr-tunnel influxdb3.0001200.xyz
cloudflared tunnel route dns wfr-tunnel influxdb2.0001200.xyz
cloudflared tunnel route dns wfr-tunnel grafana.0001200.xyz

# Run the tunnel
cloudflared tunnel --config .\config.yml run wfr-tunnel