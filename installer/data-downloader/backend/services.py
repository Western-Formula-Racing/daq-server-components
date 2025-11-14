from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Dict, Optional

from influxdb_client_3 import InfluxDBClient3

from backend.config import Settings
from backend.storage import RunsRepository, SensorsRepository, ScannerStatusRepository
from backend.influx_queries import fetch_signal_series
from backend.server_scanner import ScannerConfig, scan_runs
from backend.sql import SensorQueryConfig, fetch_unique_sensors


logger = logging.getLogger(__name__)


def _parse_iso(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class DataDownloaderService:
    def __init__(self, settings: Settings):
        self.settings = settings
        data_dir = Path(settings.data_dir).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        self.runs_repo = RunsRepository(data_dir)
        self.sensors_repo = SensorsRepository(data_dir)
        self.status_repo = ScannerStatusRepository(data_dir)
        self._log_influx_connectivity()

    def get_runs(self) -> dict:
        return self.runs_repo.list_runs()

    def get_sensors(self) -> dict:
        return self.sensors_repo.list_sensors()

    def update_note(self, key: str, note: str) -> dict | None:
        return self.runs_repo.update_note(key, note)

    def get_scanner_status(self) -> dict:
        return self.status_repo.get_status()

    def run_full_scan(self, source: str = "manual") -> Dict[str, dict]:
        self.status_repo.mark_start(source)
        try:
            runs = scan_runs(
                ScannerConfig(
                    host=self.settings.influx_host,
                    token=self.settings.influx_token,
                    database=self.settings.influx_database,
                    table=f"{self.settings.influx_schema}.{self.settings.influx_table}",
                    year=self.settings.scanner_year,
                    bin_size=self.settings.scanner_bin,
                    include_counts=self.settings.scanner_include_counts,
                    initial_chunk_days=self.settings.scanner_initial_chunk_days,
                )
            )
            runs_payload = self.runs_repo.merge_scanned_runs(runs)

            sensors = fetch_unique_sensors(
                SensorQueryConfig(
                    host=self.settings.influx_host,
                    token=self.settings.influx_token,
                    database=self.settings.influx_database,
                    schema=self.settings.influx_schema,
                    table=self.settings.influx_table,
                    window_days=self.settings.sensor_window_days,
                    lookback_days=self.settings.sensor_lookback_days,
                    fallback_start=_parse_iso(self.settings.sensor_fallback_start),
                    fallback_end=_parse_iso(self.settings.sensor_fallback_end),
                )
            )
            sensors_payload = self.sensors_repo.write_sensors(sensors)

            self.status_repo.mark_finish(success=True)

            return {
                "runs": runs_payload,
                "sensors": sensors_payload,
            }
        except Exception as exc:
            self.status_repo.mark_finish(success=False, error=str(exc))
            raise

    def query_signal_series(self, signal: str, start: datetime, end: datetime, limit: Optional[int]) -> dict:
        return fetch_signal_series(self.settings, signal, start, end, limit)

    def _log_influx_connectivity(self) -> None:
        host = self.settings.influx_host
        database = self.settings.influx_database
        table = f"{self.settings.influx_schema}.{self.settings.influx_table}"
        try:
            logger.info(
                "Checking InfluxDB connectivity (host=%s, database=%s, table=%s)",
                host,
                database,
                table,
            )
            with InfluxDBClient3(host=host, token=self.settings.influx_token, database=database) as client:
                ping_fn = getattr(client, "ping", None)
                if callable(ping_fn):
                    ping_fn()
                else:
                    client.query("SELECT 1")
            logger.info("InfluxDB connectivity OK")
        except Exception:
            logger.exception("InfluxDB connectivity check failed")
