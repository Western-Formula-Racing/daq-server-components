from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Set

from influxdb_client_3 import InfluxDBClient3

from table_utils import quote_table

UTC = timezone.utc


@dataclass(frozen=True)
class SensorQueryConfig:
    host: str
    token: str
    database: str
    schema: str
    table: str
    window_days: int = 7
    lookback_days: int = 30

    @property
    def table_ref(self) -> str:
        identifier = f"{self.schema}.{self.table}" if self.schema else self.table
        return quote_table(identifier)


def fetch_unique_sensors(config: SensorQueryConfig) -> List[str]:
    """Collect distinct signal names by scanning the recent history."""
    end = datetime.now(UTC)
    start = end - timedelta(days=config.lookback_days)
    unique: Set[str] = set()

    with InfluxDBClient3(host=config.host, token=config.token, database=config.database) as client:
        cur = start
        while cur < end:
            nxt = min(cur + timedelta(days=config.window_days), end)
            sql = f"""
                SELECT DISTINCT "signalName"
                FROM {config.table_ref}
                WHERE time >= TIMESTAMP '{cur.isoformat()}'
                  AND time <  TIMESTAMP '{nxt.isoformat()}'
                LIMIT 5000
            """
            try:
                tbl = client.query(sql)
                if tbl.num_rows == 0:
                    cur = nxt
                    continue
                for row in tbl.column("signalName"):
                    unique.add(row.as_py())
            except Exception:
                # keep the process resilient by skipping the failing window
                pass
            cur = nxt
    return sorted(unique)


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
