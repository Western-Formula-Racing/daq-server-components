"""
Server scanner — discovers data availability windows using slicks.

Wraps slicks.scan_data_availability() and converts its ScanResult
into the flat list-of-dicts format consumed by the data-downloader API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import md5
from typing import List, Optional

import slicks

UTC = timezone.utc


@dataclass(frozen=True)
class ScannerConfig:
    """Configuration for a data-availability scan."""

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
    def start(self) -> datetime:
        return datetime(self.year, 1, 1, tzinfo=UTC)

    @property
    def end(self) -> datetime:
        return datetime(self.year + 1, 1, 1, tzinfo=UTC)


def scan_runs(config: ScannerConfig) -> List[dict]:
    """Scan InfluxDB for data windows and return API-ready dicts.

    Delegates all scanning logic to ``slicks.scan_data_availability``
    then reshapes the result for the data-downloader API.
    """
    slicks.connect_influxdb3(
        url=config.host,
        token=config.token,
        db=config.database,
    )

    result = slicks.scan_data_availability(
        start=config.start,
        end=config.end,
        timezone=config.timezone_name,
        table=config.table,
        bin_size=config.bin_size,
        include_counts=config.include_counts,
        show_progress=False,
    )

    return _scan_result_to_dicts(result, config)


def _scan_result_to_dicts(result, config: ScannerConfig) -> List[dict]:
    """Convert a slicks ScanResult into the flat list format the API expects."""
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


def _build_key(start_dt_utc: datetime, end_dt_utc: datetime) -> str:
    raw = f"{start_dt_utc.isoformat()}_{end_dt_utc.isoformat()}"
    return md5(raw.encode()).hexdigest()[:10]


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
