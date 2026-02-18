from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Dict, List, Optional

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
        
        # Repositories keyed by season name (e.g. "WFR25")
        self.runs_repos: Dict[str, RunsRepository] = {}
        self.sensors_repos: Dict[str, SensorsRepository] = {}
        
        for season in settings.seasons:
            # Suffix file with season name: runs_WFR25.json
            self.runs_repos[season.name] = RunsRepository(data_dir, suffix=season.name)
            self.sensors_repos[season.name] = SensorsRepository(data_dir, suffix=season.name)
            
        self.status_repo = ScannerStatusRepository(data_dir)
        self._log_influx_connectivity()

    def get_runs(self, season: str | None = None) -> dict:
        target_season = season or self._default_season()
        repo = self.runs_repos.get(target_season)
        if not repo:
            return {"runs": [], "error": f"Season {target_season} not found"}
        return repo.list_runs()

    def get_sensors(self, season: str | None = None) -> dict:
        target_season = season or self._default_season()
        repo = self.sensors_repos.get(target_season)
        if not repo:
            return {"sensors": [], "error": f"Season {target_season} not found"}
        return repo.list_sensors()

    def update_note(self, key: str, note: str, season: str | None = None) -> dict | None:
        target_season = season or self._default_season()
        repo = self.runs_repos.get(target_season)
        if not repo:
            return None
        return repo.update_note(key, note)

    def get_scanner_status(self) -> dict:
        return self.status_repo.get_status()
        
    def get_seasons(self) -> List[dict]:
        """Return list of available seasons."""
        return [
            {"name": s.name, "year": s.year, "database": s.database, "color": s.color}
            for s in self.settings.seasons
        ]

    def run_full_scan(self, source: str = "manual") -> Dict[str, dict]:
        self.status_repo.mark_start(source)
        results = {}
        errors = []
        
        try:
            # Sort seasons by year descending to ensure most recent is scanned first
            sorted_seasons = sorted(self.settings.seasons, key=lambda s: s.year, reverse=True)
            for season in sorted_seasons:
                try:
                    logger.info(f"Scanning season {season.name} (DB: {season.database})...")
                    
                    runs = scan_runs(
                        ScannerConfig(
                            host=self.settings.influx_host,
                            token=self.settings.influx_token,
                            database=season.database,
                            table=f"{self.settings.influx_schema}.{self.settings.influx_table}",
                            year=season.year,
                            bin_size=self.settings.scanner_bin,
                            include_counts=self.settings.scanner_include_counts,
                            initial_chunk_days=self.settings.scanner_initial_chunk_days,
                        )
                    )
                    
                    repo_runs = self.runs_repos[season.name]
                    runs_payload = repo_runs.merge_scanned_runs(runs)
                    
                    fallback_start, fallback_end = self._build_sensor_fallback_range(runs)
                    
                    sensors = fetch_unique_sensors(
                        SensorQueryConfig(
                            host=self.settings.influx_host,
                            token=self.settings.influx_token,
                            database=season.database,
                            schema=self.settings.influx_schema,
                            table=self.settings.influx_table,
                            window_days=self.settings.sensor_window_days,
                            lookback_days=self.settings.sensor_lookback_days,
                            fallback_start=fallback_start,
                            fallback_end=fallback_end,
                        )
                    )
                    repo_sensors = self.sensors_repos[season.name]
                    sensors_payload = repo_sensors.write_sensors(sensors)
                    
                    results[season.name] = {
                        "runs": len(runs_payload.get("runs", [])),
                        "sensors": len(sensors_payload.get("sensors", []))
                    }
                    
                except Exception as e:
                    logger.exception(f"Failed to scan season {season.name}")
                    errors.append(f"{season.name}: {str(e)}")
                    # Continue scanning other seasons even if one fails
            
            if errors:
                self.status_repo.mark_finish(success=False, error="; ".join(errors))
            else:
                self.status_repo.mark_finish(success=True)

            return results
            
        except Exception as exc:
            self.status_repo.mark_finish(success=False, error=str(exc))
            raise

    def query_signal_series(self, signal: str, start: datetime, end: datetime, limit: Optional[int], season: str | None = None) -> dict:
        target_season_name = season or self._default_season()
        season_cfg = next((s for s in self.settings.seasons if s.name == target_season_name), None)
        
        if not season_cfg:
             raise ValueError(f"Season {target_season_name} not configured")
             
        # Temporarily override settings with season database for the query
        # This is a bit hacky but avoids refactoring fetch_signal_series signature deeper
        # Ideally fetch_signal_series should take db name argument
        
        # Actually fetch_signal_series takes 'settings' object. 
        # We can construct a proxy or just rely on the existing signature if we modify it.
        # But modify backend/influx_queries.py is safer. 
        # For now, let's assume fetch_signal_series uses settings.influx_database. 
        # We need to pass the correct DB.
        
        return fetch_signal_series(self.settings, signal, start, end, limit, database=season_cfg.database)

    def _default_season(self) -> str:
        # Default to the first (newest) season if available
        if self.settings.seasons:
            return self.settings.seasons[0].name
        return "WFR25"

    def _log_influx_connectivity(self) -> None:
        # Check connectivity for the default season
        season = self.settings.seasons[0] if self.settings.seasons else None
        if not season:
            return

        host = self.settings.influx_host
        database = season.database
        try:
            logger.info("Checking InfluxDB connectivity (%s -> %s)", host, database)
            with InfluxDBClient3(host=host, token=self.settings.influx_token, database=database) as client:
                getattr(client, "ping", lambda: client.query("SELECT 1"))()
            logger.info("InfluxDB connectivity OK")
        except Exception:
            logger.exception("InfluxDB connectivity check failed")

    @staticmethod
    def _build_sensor_fallback_range(runs: List[dict]) -> tuple[Optional[datetime], Optional[datetime]]:
        """Use the longest run discovered by the scanner for sensor fallback."""
        longest_run: Optional[dict] = None
        longest_duration: Optional[float] = None
        
        for run in runs:
            start_dt = _parse_iso(run.get("start_utc"))
            end_dt = _parse_iso(run.get("end_utc"))
            if start_dt is None or end_dt is None:
                continue
            duration = (end_dt - start_dt).total_seconds()
            if longest_duration is None or duration > longest_duration:
                longest_duration = duration
                longest_run = run
        
        if longest_run is None:
            return None, None
        
        fallback_start = _parse_iso(longest_run.get("start_utc"))
        fallback_end = _parse_iso(longest_run.get("end_utc"))
        return fallback_start, fallback_end
