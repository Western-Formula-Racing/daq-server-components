<img width="3456" height="605" alt="WFR-DAQ-Banner" src="https://github.com/user-attachments/assets/2e05c619-8101-4062-9a7d-6f40dfdc7fad" />

# Western Formula Racing DAQ Components

This repository hosts the Docker-based telemetry stack that powers Western Formula Racing’s data acquisition (DAQ) pipeline. It is designed to be publicly shareable: all runtime credentials live in `.env` files, sample datasets are anonymised, and every container is documented for easy onboarding.

## Repository layout

| Path | Description |
| --- | --- |
| `installer/` | Docker Compose deployment, container sources, and environment templates. |
| `docs/` | Public-facing documentation for each service and the compose stack. |
| `dev-utils` | Development utility scripts (not for production use). |
## Quick start

1. Install Docker Desktop (macOS/Windows) or Docker Engine + Compose V2 (Linux).
2. Navigate to the installer and copy the environment template:
   ```bash
   cd installer
   cp .env.example .env
   # Update values before deploying outside of local development
   ```
3. Launch the stack:
   ```bash
   docker compose up -d
   ```
4. Visit the services:
   - InfluxDB 3 Explorer – http://localhost:8888
   - Grafana – http://localhost:8087
   - Drag and Drop CSV File uploader – http://localhost:8084
   - Data Downloader - http://localhost:3000

All services share a bridge network named `datalink` and rely on the admin token supplied through `.env`.

## System architecture

```mermaid
graph TB
    subgraph Ingestion["Data Ingestion"]
        CSV["CSV Log Files"]
        DBC["DBC File<br/><i>CAN Signal Definitions</i>"]
        SDL["Startup Data Loader<br/><i>Bulk loader on boot</i>"]
        FU["File Uploader<br/><i>Drag & drop web UI :8084</i>"]
    end

    subgraph Radio["daq-radio repo (external)"]
        CAR["Car ECU, Raspberry Pi<br/><i>CAN → radio transmitter</i>"]
        BASE["Base Station<br/><i>UDP/TCP receiver</i>"]
    end

    subgraph Storage["Time-Series Storage"]
        INFLUX["InfluxDB 3<br/><i>Core database :9000</i>"]
    end

    subgraph Visualization["Visualization & Exploration"]
        EXPLORER["InfluxDB 3 Explorer<br/><i>Query browser :8888</i>"]
        GRAFANA["Grafana<br/><i>Dashboards :8087</i>"]
    end

    subgraph DataExport["Data Downloader :3000"]
        DD_FE["Frontend<br/><i>Vite + TypeScript SPA</i>"]
        DD_API["Backend API<br/><i>FastAPI :8000</i>"]
        DD_SCAN["Periodic Scanner<br/><i>Discovers runs & signals</i>"]
    end

    subgraph AI["AI Analysis Pipeline"]
        SLACK["Slackbot<br/><i>Lappy — Socket Mode</i>"]
        CODEGEN["Code Generator<br/><i>Cohere LLM :3030</i>"]
        SANDBOX["Sandbox<br/><i>Isolated Python runner</i>"]
    end

    subgraph Tracking["Future: Track Analysis"]
        LAP["Lap Detector<br/><i>Dash app :8050<br/>Planned — requires GPS hardware</i>"]
    end

    CSV --> SDL
    CSV --> FU
    DBC -.->|cantools decode| SDL
    DBC -.->|cantools decode| FU
    SDL -->|Write API| INFLUX
    FU -->|Write API| INFLUX

    CAR -.->|UDP/TCP| BASE
    BASE -.->|Write API| INFLUX

    INFLUX --> EXPLORER
    INFLUX --> GRAFANA

    DD_FE -->|REST| DD_API
    DD_API -->|SQL queries| INFLUX
    DD_SCAN -->|Discover data| INFLUX
    DD_SCAN -->|Update metadata| DD_API

    SLACK -->|"!agent prompt"| CODEGEN
    CODEGEN -->|Generated Python| SANDBOX
    SANDBOX -->|Query via env creds| INFLUX
    SANDBOX -->|stdout + images| CODEGEN
    CODEGEN -->|Results| SLACK

    SLACK -->|"!location"| LAP

    style Ingestion fill:#e8f5e9,stroke:#2e7d32,color:#000
    style Storage fill:#e3f2fd,stroke:#1565c0,color:#000
    style Visualization fill:#fff3e0,stroke:#e65100,color:#000
    style DataExport fill:#f3e5f5,stroke:#6a1b9a,color:#000
    style AI fill:#fce4ec,stroke:#b71c1c,color:#000
    style Tracking fill:#f5f5f5,stroke:#9e9e9e,color:#000,stroke-dasharray: 5 5
    style Radio fill:#f5f5f5,stroke:#9e9e9e,color:#000,stroke-dasharray: 5 5
```

## System overview

The compose stack deploys eight cooperating containers:

1. **InfluxDB 3** – Time-series database seeded with a tiny example dataset.
2. **InfluxDB 3 Explorer** – Web UI for browsing and querying telemetry.
3. **Grafana** – Pre-provisioned dashboards that visualise the stored telemetry.  Load your own dashboard provisioning files into `installer/grafana/dashboards/`.
4. **Sandbox** - *Under active development.* Connecting InfluxDB3 with LLM for natural language queries and analysis.
5. **Slack bot d.b.a. Lappy** – Optional automation/notification bot for race ops.
6. **Lap analysis app** – *Under active development.* Dash-based location data visualiser and lap timer. (Useful if GPS data is available.) 
7. **Startup data loader** – Seeds the database on boot with sample CAN frames.
8. **File uploader** – Streams uploaded CSV logs into InfluxDB using the shared DBC file.
9. **Data downloader** - Scans InfluxDB periodically, visual SQL query builder, and CSV export service.

Detailed documentation for each service is available in `docs/containers/`.

## Sample data & DBC files

The repository ships with `example.dbc` (a minimal CAN database) and a sample dataset (`2025-01-01-00-00-00.csv`) containing four rows of synthetic telemetry. Replace both assets with production data when working with real vehicles.

## Working with environment variables

Every container reads its credentials from the `.env` file co-located with `docker-compose.yml`. Refer to `installer/.env.example` for the exhaustive list. Never commit real tokens—keep personal overrides in `.env` and add `.env` to your global gitignore.

## Documentation index

- [Compose stack reference](docs/docker-compose.md)
- [Container documentation](docs/containers/)
- [Grafana dashboards](installer/grafana/)
- [Startup data loader](installer/startup-data-loader/README.md)

## Contributing

TBD

## Hardware Dependencies
https://github.com/Western-Formula-Racing/ECU_25


## Acknowledgements
This project was developed in 2024 and maintained by the Western Formula Racing Data Acquisition team, inspired by the team's prior work on telemetry systems for our Formula SAE vehicles.
https://github.com/Western-Formula-Racing/RaspberryPi-CAN-DAQ-MVP
https://github.com/Western-Formula-Racing/daq-2023


We also want to acknowledge the open-source tools and libraries that make this project possible. Key components include:
* Docker / Docker Compose for containerisation
* InfluxDB 3 for time-series storage
* Grafana for visualisation
* Python open-source packages (NumPy, Pandas, Requests, etc.) used throughout the stack

### Explore more work from Western Formula Racing

If you’re interested in our team’s broader engineering projects, here are some of the hardware systems developed alongside this DAQ stack:
* https://github.com/Western-Formula-Racing/ECU_25
* https://github.com/Western-Formula-Racing/Custom-BMS_25
* https://github.com/Western-Formula-Racing/mobo-25

## Under Active Development
1. Slack bot improvements + sandbox
2. Lap analysis app


## License
AGPL-3.0 License. See LICENSE file for details.