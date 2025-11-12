from __future__ import annotations

from datetime import datetime, timezone

from influxdb_client_3 import InfluxDBClient3

from backend.config import Settings
from table_utils import quote_literal, quote_table


def _normalize(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fetch_signal_series(settings: Settings, signal: str, start: datetime, end: datetime, limit: int) -> dict:
    start_dt = _normalize(start)
    end_dt = _normalize(end)
    if start_dt >= end_dt:
        raise ValueError("start must be before end")
    limit = max(10, min(limit, 20000))

    table_ref = quote_table(f"{settings.influx_schema}.{settings.influx_table}")
    signal_literal = quote_literal(signal)

    sql = f"""
        SELECT time, "sensorReading"
        FROM {table_ref}
        WHERE "signalName" = {signal_literal}
          AND time >= TIMESTAMP '{start_dt.isoformat()}'
          AND time <= TIMESTAMP '{end_dt.isoformat()}'
        ORDER BY time
        LIMIT {limit}
    """

    with InfluxDBClient3(host=settings.influx_host, token=settings.influx_token, database=settings.influx_database) as client:
        tbl = client.query(sql)
        points = []
        for idx in range(tbl.num_rows):
            ts = tbl.column("time")[idx].as_py()
            value = tbl.column("sensorReading")[idx].as_py()
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)
            points.append(
                {
                    "time": ts.isoformat(),
                    "value": float(value),
                }
            )

    return {
        "signal": signal,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "limit": limit,
        "row_count": len(points),
        "points": points,
    }
