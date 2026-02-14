"""
Sensor discovery — finds unique signal names using slicks.

Wraps slicks.discover_sensors() with the lookback / fallback
logic required by the data-downloader API.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import List, Optional

import slicks

UTC = timezone.utc


@dataclass(frozen=True)
class SensorQueryConfig:
    """Configuration for sensor discovery."""

    host: str
    token: str
    database: str
    schema: str
    table: str
    window_days: int = 7
    lookback_days: int = 30
    fallback_start: datetime | None = None
    fallback_end: datetime | None = None


def fetch_unique_sensors(config: SensorQueryConfig) -> List[str]:
    """Collect distinct signal names by scanning recent history.

    First searches the last ``lookback_days``.  If nothing is found,
    falls back to the scanner-discovered time range.
    """
    slicks.connect_influxdb3(
        url=config.host,
        token=config.token,
        db=config.database,
    )

    end = datetime.now(UTC)
    start = end - timedelta(days=config.lookback_days)

    sensors = slicks.discover_sensors(
        start_time=start,
        end_time=end,
        chunk_size_days=config.window_days,
    )

    if not sensors and config.fallback_start and config.fallback_end:
        sensors = slicks.discover_sensors(
            start_time=config.fallback_start,
            end_time=config.fallback_end,
            chunk_size_days=config.window_days,
        )

    return sensors


if __name__ == "__main__":  # pragma: no cover
    import json
    import os

    cfg = SensorQueryConfig(
        host=os.getenv("INFLUX_HOST", "http://localhost:9000"),
        token=os.getenv("INFLUX_TOKEN", ""),
        database=os.getenv("INFLUX_DATABASE", "WFR25"),
        schema=os.getenv("INFLUX_SCHEMA", "iox"),
        table=os.getenv("INFLUX_TABLE", "WFR25"),
    )
    print(json.dumps(fetch_unique_sensors(cfg), indent=2))
