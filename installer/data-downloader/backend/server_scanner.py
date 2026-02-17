"""Thin wrapper that delegates run scanning to the *slicks* package.

The public API (``ScannerConfig`` + ``scan_runs``) is unchanged so the
rest of the backend continues to work without modification.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import md5
from typing import List

from zoneinfo import ZoneInfo

import slicks
from slicks.scanner import scan_data_availability

UTC = timezone.utc


@dataclass(frozen=True)
class ScannerConfig:
    host: str
    token: str
    database: str
    table: str
    year: int = 2025
    bin_size: str = "hour"  # hour or day
    include_counts: bool = True
    initial_chunk_days: int = 31
    timezone_name: str = "America/Toronto"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def start(self) -> datetime:
        # Season starts in August of the previous year
        return datetime(self.year - 1, 8, 1, tzinfo=UTC)

    @property
    def end(self) -> datetime:
        # Season ends at the end of the configured year (Jan 1 of year + 1)
        return datetime(self.year + 1, 1, 1, tzinfo=UTC)


def _build_key(start_dt_utc: datetime, end_dt_utc: datetime) -> str:
    raw = f"{start_dt_utc.isoformat()}_{end_dt_utc.isoformat()}"
    return md5(raw.encode()).hexdigest()[:10]


def scan_runs(config: ScannerConfig) -> List[dict]:
    """Run the adaptive scan via *slicks* and return formatted windows."""

    # Configure slicks to point at the same InfluxDB instance
    slicks.connect_influxdb3(
        url=config.host,
        token=config.token,
        db=config.database,
    )

    # Determine the table string slicks expects ("schema.table")
    table = config.table  # already "iox.WFR25" from services.py

    result = scan_data_availability(
        start=config.start,
        end=config.end,
        timezone=config.timezone_name,
        table=table,
        bin_size=config.bin_size,
        include_counts=config.include_counts,
        show_progress=False,
    )

    # Convert ScanResult → List[dict] matching the old format
    formatted: List[dict] = []
    for _day, windows in result:
        for w in windows:
            entry = {
                "key": _build_key(w.start_utc, w.end_utc),
                "start_utc": w.start_utc.isoformat(),
                "end_utc": w.end_utc.isoformat(),
                "start_local": w.start_local.isoformat(),
                "end_local": w.end_local.isoformat(),
                "timezone": config.timezone_name,
                "bins": w.bins,
            }
            if config.include_counts:
                entry["row_count"] = w.row_count
            formatted.append(entry)

    return formatted


if __name__ == "__main__":  # pragma: no cover
    import json
    import os

    schema = os.getenv("INFLUX_SCHEMA", "iox")
    table = os.getenv("INFLUX_TABLE", "WFR25")

    cfg = ScannerConfig(
        host=os.getenv("INFLUX_HOST", "http://localhost:9000"),
        token=os.getenv("INFLUX_TOKEN", ""),
        database=os.getenv("INFLUX_DATABASE", "WFR25"),
        table=f"{schema}.{table}",
    )
    print(json.dumps(scan_runs(cfg), indent=2))
