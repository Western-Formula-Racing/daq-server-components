# Docker Error Logger Setup

This document describes how to set up a systemd service to continuously log Docker container stderr output and filter for `error` messages.

## Prerequisites

- Linux system with systemd (e.g., Ubuntu, Debian, CentOS).
- Docker installed and running.
- User with `sudo` privileges.

## 1. Create the Logging Script

Save the following script as `/usr/local/bin/log_docker_errors.sh`:

```bash
#!/usr/bin/env bash
LOG_DIR="/var/log/docker-errors"
mkdir -p "$LOG_DIR"

# For each running container, start a background tail-and-grep
for container in $(docker ps --format '{{.Names}}'); do
  # --since 1s to avoid re-processing old logs; -f to follow
  docker logs -f --since 1s "$container" 2>&1 \
  | grep --line-buffered 'level=error' \
  >> "$LOG_DIR/${container}.err.log" &
done

# Wait on all background jobs so this script never exits
wait
```

Make the script executable:

```bash
sudo chmod +x /usr/local/bin/log_docker_errors.sh
```

## 2. Create the systemd Service Unit

Create `/etc/systemd/system/docker-error-logger.service` with the following content:

```ini
[Unit]
Description=Log stderr of Docker containers to /var/log/docker-errors
After=docker.service
Requires=docker.service

[Service]
Type=simple
ExecStart=/usr/local/bin/log_docker_errors.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## 3. Enable and Start the Service

Reload systemd, enable the service to start on boot, and start it now:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now docker-error-logger.service
```

Verify the service is active:

```bash
systemctl status docker-error-logger.service
```

You should see `active (running)` and child processes corresponding to each container’s `docker logs -f` and `grep`.

## 4. Verify Log Files

The filtered logs are stored under `/var/log/docker-errors/`. For example:

```bash
ls /var/log/docker-errors
tail -f /var/log/docker-errors/grafana.err.log
```

## 5. (Optional) Log Rotation

To prevent log files from growing indefinitely, create a logrotate configuration `/etc/logrotate.d/docker-errors`:

```text
/var/log/docker-errors/*.err.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
}
```

This rotates the logs daily, keeps seven days of logs, compresses old logs, and uses `copytruncate` to avoid service interruption.
