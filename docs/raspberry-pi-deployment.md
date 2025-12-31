# Raspberry Pi Production Deployment

This guide explains how to deploy the DAQ system on a Raspberry Pi using pre-built Docker images from GitHub Container Registry, avoiding the need to build images locally.

## Prerequisites

- Raspberry Pi with Docker and Docker Compose installed
- Internet connection to pull images from GitHub Container Registry
- Git installed

## Quick Start

1. **Clone the repository:**
   ```bash
   git clone https://github.com/westernformularacing/daq-server-components.git
   cd daq-server-components/installer
   ```

2. **Create your environment file:**
   ```bash
   cp .env.example .env
   ```
   
3. **Edit `.env` with your settings** (optional - defaults work for testing):
   ```bash
   nano .env
   ```

4. **Pull and start services using pre-built images:**
   ```bash
   docker compose -f docker-compose.prod.yml pull
   docker compose -f docker-compose.prod.yml up -d
   ```

## What's Different in Production?

**Development (`docker-compose.yml`):**
- Builds images locally from source (slow on RPi)
- Mounts source code for live development
- Uses `build:` directives

**Production (`docker-compose.prod.yml`):**
- Pulls pre-built images from GitHub Container Registry (fast!)
- No building required
- Uses `image:` directives pointing to GHCR

## Configuration Options

### Use Specific Image Version

By default, it pulls `:latest` images. To use a specific commit/version:

```bash
# In .env file:
IMAGE_TAG=abc123def456  # Use specific git commit SHA
```

Or temporarily:
```bash
IMAGE_TAG=abc123def456 docker compose -f docker-compose.prod.yml up -d
```

## Updating to Latest Images

```bash
cd /path/to/daq-server-components/installer
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

## Checking Service Status

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f
```

## Accessing Services

Once running, access:
- **InfluxDB3**: http://raspberry-pi-ip:9000
- **InfluxDB3 Explorer**: http://raspberry-pi-ip:8888
- **Grafana**: http://raspberry-pi-ip:8087
- **File Uploader**: http://raspberry-pi-ip:8084
- **Data Downloader**: http://raspberry-pi-ip:3000
- **Lap Detector**: http://raspberry-pi-ip:8050

## Stopping Services

```bash
docker compose -f docker-compose.prod.yml down
```

To also remove volumes (careful - deletes data!):
```bash
docker compose -f docker-compose.prod.yml down -v
```

## Troubleshooting

### Images Won't Pull
- Check internet connection
- Verify GitHub Actions successfully built and pushed images
- Check if repository is private (need authentication)

### Services Failing to Start
- Check logs: `docker compose -f docker-compose.prod.yml logs service-name`
- Verify `.env` file exists and has correct values
- Ensure required volumes/files exist (e.g., `example.dbc`)

### Out of Space
```bash
# Clean up old images
docker system prune -a
```

## Build Triggers

Images are automatically built and pushed to GHCR when:
- You push to the `deploy` branch
- You create a pull request to `main` or `deploy` branches

Check the Actions tab in GitHub to see build status.
