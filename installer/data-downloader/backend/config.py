from __future__ import annotations

from functools import lru_cache
import os
from typing import List
from pydantic import BaseModel, Field


def _parse_origins(raw: str | None) -> List[str]:
    if not raw or raw.strip() == "*":
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


class Settings(BaseModel):
    """Centralised configuration pulled from environment variables."""

    data_dir: str = Field(default_factory=lambda: os.getenv("DATA_DIR", "./data"))

    influx_host: str = Field(default_factory=lambda: os.getenv("INFLUX_HOST", "http://localhost:9000"))
    influx_token: str = Field(default_factory=lambda: os.getenv("INFLUX_TOKEN", ""))
    influx_database: str = Field(default_factory=lambda: os.getenv("INFLUX_DATABASE", "WFR25"))
    influx_schema: str = Field(default_factory=lambda: os.getenv("INFLUX_SCHEMA", "iox"))
    influx_table: str = Field(default_factory=lambda: os.getenv("INFLUX_TABLE", "WFR25"))

    scanner_year: int = Field(default_factory=lambda: int(os.getenv("SCANNER_YEAR", "2025")))
    scanner_bin: str = Field(default_factory=lambda: os.getenv("SCANNER_BIN", "hour"))  # hour or day
    scanner_include_counts: bool = Field(default_factory=lambda: os.getenv("SCANNER_INCLUDE_COUNTS", "true").lower() == "true")
    scanner_initial_chunk_days: int = Field(default_factory=lambda: int(os.getenv("SCANNER_INITIAL_CHUNK_DAYS", "31")))

    sensor_window_days: int = Field(default_factory=lambda: int(os.getenv("SENSOR_WINDOW_DAYS", "7")))
    sensor_lookback_days: int = Field(default_factory=lambda: int(os.getenv("SENSOR_LOOKBACK_DAYS", "30")))
    sensor_fallback_start: str | None = Field(default_factory=lambda: os.getenv("SENSOR_FALLBACK_START", "2025-06-19T00:00:00"))
    sensor_fallback_end: str | None = Field(default_factory=lambda: os.getenv("SENSOR_FALLBACK_END", "2025-07-10T00:00:00"))

    periodic_interval_seconds: int = Field(default_factory=lambda: int(os.getenv("SCAN_INTERVAL_SECONDS", "3600")))

    allowed_origins: List[str] = Field(default_factory=lambda: _parse_origins(os.getenv("ALLOWED_ORIGINS", "*")))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cache settings so the same instance is reused across the app."""
    return Settings()
