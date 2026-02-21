#!/usr/bin/env python3
"""
Health monitor: collects Docker container and application metrics,
writes them to an InfluxDB 3 bucket every 60 seconds.
"""

from __future__ import annotations

import os
import sys
import time
import logging
from datetime import datetime, timezone

import docker
import requests
from influxdb_client_3 import InfluxDBClient3, Point

# Config from environment
INTERVAL_SECONDS = int(os.getenv("HEALTH_MONITOR_INTERVAL_SECONDS", "60"))
INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://influxdb3:8181")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_ADMIN_TOKEN", os.getenv("INFLUXDB_TOKEN", ""))
INFLUXDB_DATABASE = os.getenv("INFLUXDB_HEALTH_DATABASE", "health")
CONTAINER_INFLUXDB = os.getenv("HEALTH_MONITOR_INFLUXDB_CONTAINER", "influxdb3")
CONTAINER_SCANNER = os.getenv("HEALTH_MONITOR_SCANNER_CONTAINER", "data-downloader-scanner")
SCANNER_API_URL = os.getenv(
    "HEALTH_MONITOR_SCANNER_API_URL",
    "http://data-downloader-api:8000",
)
INFLUXDB_VOLUME_NAME_SUFFIX = os.getenv("HEALTH_MONITOR_INFLUXDB_VOLUME_SUFFIX", "influxdb3-data")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def _now_ns() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)


def _influx_client_kwargs() -> dict:
    """Connection kwargs for InfluxDBClient3 (host may be URL or host:port)."""
    url = (INFLUXDB_URL or "").strip().rstrip("/")
    return {
        "host": url,
        "token": INFLUXDB_TOKEN,
        "database": INFLUXDB_DATABASE,
        "org": "",
    }


def collect_influxdb_metrics(client: docker.DockerClient) -> dict:
    """Collect metrics for the InfluxDB container: status, restart count, disk usage, write latency."""
    out = {
        "up": False,
        "restart_count": None,
        "disk_usage_bytes": None,
        "write_latency_seconds": None,
        "write_error": None,
    }
    try:
        container = client.containers.get(CONTAINER_INFLUXDB)
        out["up"] = container.attrs["State"]["Running"]
        out["restart_count"] = container.attrs.get("RestartCount", 0)
    except docker.errors.NotFound:
        logger.warning("Container %s not found", CONTAINER_INFLUXDB)
        return out
    except Exception as e:
        logger.exception("Error inspecting %s: %s", CONTAINER_INFLUXDB, e)
        out["write_error"] = str(e)
        return out

    # Disk usage: find volume matching suffix in Docker system df
    try:
        df = client.api.df()
        for vol in df.get("Volumes") or []:
            name = vol.get("Name") or ""
            if INFLUXDB_VOLUME_NAME_SUFFIX in name or name.endswith("_" + INFLUXDB_VOLUME_NAME_SUFFIX):
                usage = (vol.get("UsageData") or {}).get("Size")
                if usage is not None:
                    out["disk_usage_bytes"] = usage
                break
    except Exception as e:
        logger.debug("Could not get volume disk usage: %s", e)

    # Write latency: time a single point write
    if out["up"] and INFLUXDB_TOKEN:
        try:
            start = time.perf_counter()
            with InfluxDBClient3(**(_influx_client_kwargs())) as influx:
                ping = Point("health_ping").field("check", 1).time(_now_ns(), write_precision="ns")
                influx.write(ping)
            out["write_latency_seconds"] = round(time.perf_counter() - start, 4)
        except Exception as e:
            out["write_error"] = str(e)[:500]
            logger.debug("InfluxDB latency check failed: %s", e)

    return out


def collect_scanner_metrics(client: docker.DockerClient) -> dict:
    """Collect metrics for the scanner container: status and app metrics from API."""
    out = {
        "up": False,
        "events_processed_per_minute": None,
        "last_successful_job_timestamp": None,
        "error_count": None,
        "api_error": None,
    }
    try:
        container = client.containers.get(CONTAINER_SCANNER)
        out["up"] = container.attrs["State"]["Running"]
    except docker.errors.NotFound:
        logger.warning("Container %s not found", CONTAINER_SCANNER)
        return out
    except Exception as e:
        logger.exception("Error inspecting %s: %s", CONTAINER_SCANNER, e)
        out["api_error"] = str(e)
        return out

    # Application metrics from data-downloader API (reads shared scanner_status.json)
    try:
        r = requests.get(
            f"{SCANNER_API_URL.rstrip('/')}/api/scanner-status",
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        out["events_processed_per_minute"] = data.get("events_processed_per_minute")
        out["last_successful_job_timestamp"] = data.get("last_successful_job_timestamp")
        out["error_count"] = data.get("error_count")
    except requests.RequestException as e:
        out["api_error"] = str(e)[:500]
        logger.debug("Scanner API request failed: %s", e)
    except (ValueError, KeyError) as e:
        out["api_error"] = str(e)[:500]

    return out


def write_health_to_influx(influx_metrics: dict, scanner_metrics: dict) -> None:
    """Write collected metrics to InfluxDB 3 as points."""
    if not INFLUXDB_TOKEN:
        logger.warning("INFLUXDB_ADMIN_TOKEN/INFLUXDB_TOKEN not set; skipping write")
        return
    try:
        with InfluxDBClient3(**_influx_client_kwargs()) as client:
            ts_ns = _now_ns()

            # Container: influxdb
            p_influx = (
                Point("container_health")
                .tag("container", CONTAINER_INFLUXDB)
                .field("up", influx_metrics["up"])
                .time(ts_ns, write_precision="ns")
            )
            if influx_metrics["restart_count"] is not None:
                p_influx = p_influx.field("restart_count", influx_metrics["restart_count"])
            if influx_metrics["disk_usage_bytes"] is not None:
                p_influx = p_influx.field("disk_usage_bytes", influx_metrics["disk_usage_bytes"])
            if influx_metrics["write_latency_seconds"] is not None:
                p_influx = p_influx.field(
                    "write_latency_seconds", influx_metrics["write_latency_seconds"]
                )
            if influx_metrics.get("write_error"):
                p_influx = p_influx.field("write_error", influx_metrics["write_error"])
            client.write(p_influx)

            # Container: scanner
            p_scanner = (
                Point("container_health")
                .tag("container", CONTAINER_SCANNER)
                .field("up", scanner_metrics["up"])
                .time(ts_ns, write_precision="ns")
            )
            if scanner_metrics.get("events_processed_per_minute") is not None:
                p_scanner = p_scanner.field(
                    "events_processed_per_minute",
                    scanner_metrics["events_processed_per_minute"],
                )
            if scanner_metrics.get("last_successful_job_timestamp"):
                p_scanner = p_scanner.field(
                    "last_successful_job_timestamp",
                    scanner_metrics["last_successful_job_timestamp"],
                )
            if scanner_metrics.get("error_count") is not None:
                p_scanner = p_scanner.field("error_count", scanner_metrics["error_count"])
            if scanner_metrics.get("api_error"):
                p_scanner = p_scanner.field("api_error", scanner_metrics["api_error"])
            client.write(p_scanner)

        logger.info("Wrote health points for %s and %s", CONTAINER_INFLUXDB, CONTAINER_SCANNER)
    except Exception as e:
        logger.exception("Failed to write health to InfluxDB: %s", e)


def main() -> None:
    logger.info(
        "Health monitor started (interval=%ss, influx=%s, database=%s)",
        INTERVAL_SECONDS,
        INFLUXDB_URL,
        INFLUXDB_DATABASE,
    )
    docker_client = docker.from_env()

    while True:
        try:
            influx_metrics = collect_influxdb_metrics(docker_client)
            scanner_metrics = collect_scanner_metrics(docker_client)
            write_health_to_influx(influx_metrics, scanner_metrics)
        except Exception:
            logger.exception("Health collection cycle failed")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
