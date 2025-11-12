from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import md5
from typing import Iterable, List, Sequence, Tuple

from influxdb_client_3 import InfluxDBClient3
from zoneinfo import ZoneInfo

from table_utils import quote_table

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
        return datetime(self.year, 1, 1, tzinfo=UTC)

    @property
    def end(self) -> datetime:
        return datetime(self.year + 1, 1, 1, tzinfo=UTC)

    @property
    def interval(self) -> str:
        return "1 day" if self.bin_size == "day" else "1 hour"

    @property
    def step(self) -> timedelta:
        return timedelta(days=1) if self.bin_size == "day" else timedelta(hours=1)

    @property
    def table_ref(self) -> str:
        return quote_table(self.table)


def scan_runs(config: ScannerConfig) -> List[dict]:
    """Run the adaptive scan and return formatted windows."""
    bins = list(fetch_bins_adaptive(config))
    windows = compress_bins(bins, config.step)
    return format_windows(windows, config)


def fetch_bins_adaptive(config: ScannerConfig) -> Iterable[Tuple[datetime, int]]:
    """Iterate over bucket start times with counts."""

    def query_grouped_bins(client: InfluxDBClient3, t0: datetime, t1: datetime) -> Sequence[Tuple[datetime, int]]:
        sql = f"""
            SELECT
                DATE_BIN(INTERVAL '{config.interval}', time, TIMESTAMP '{t0.isoformat()}') AS bucket,
                COUNT(*) AS n
            FROM {config.table_ref}
            WHERE time >= TIMESTAMP '{t0.isoformat()}'
              AND time <  TIMESTAMP '{t1.isoformat()}'
            GROUP BY bucket
            HAVING COUNT(*) > 0
            ORDER BY bucket
        """
        tbl = client.query(sql)
        rows: List[Tuple[datetime, int]] = []
        for i in range(tbl.num_rows):
            bucket = tbl.column("bucket")[i].as_py()
            n = tbl.column("n")[i].as_py()
            if bucket.tzinfo is None:
                bucket = bucket.replace(tzinfo=UTC)
            else:
                bucket = bucket.astimezone(UTC)
            rows.append((bucket, int(n)))
        return rows

    def query_exists_per_bin(client: InfluxDBClient3, t0: datetime, t1: datetime) -> List[Tuple[datetime, int]]:
        cur = t0
        rows: List[Tuple[datetime, int]] = []
        while cur < t1:
            nxt = min(cur + config.step, t1)
            sql = f"""
                SELECT 1
                FROM {config.table_ref}
                WHERE time >= TIMESTAMP '{cur.isoformat()}'
                  AND time <  TIMESTAMP '{nxt.isoformat()}'
                LIMIT 1
            """
            try:
                tbl = client.query(sql)
                if tbl.num_rows > 0:
                    rows.append((cur, 1))
            except Exception:
                pass
            cur = nxt
        return rows

    def process_range(client: InfluxDBClient3, t0: datetime, t1: datetime, chunk_days: float):
        min_exists_span = config.step * 4
        if (t1 - t0) <= min_exists_span:
            for pair in query_exists_per_bin(client, t0, t1):
                yield pair
            return
        try:
            for pair in query_grouped_bins(client, t0, t1):
                yield pair
            return
        except Exception:
            mid = t0 + (t1 - t0) / 2
            if mid <= t0 or mid >= t1:
                for pair in query_exists_per_bin(client, t0, t1):
                    yield pair
                return
            for pair in process_range(client, t0, mid, chunk_days / 2):
                yield pair
            for pair in process_range(client, mid, t1, chunk_days / 2):
                yield pair

    with InfluxDBClient3(host=config.host, token=config.token, database=config.database) as client:
        cur = config.start
        while cur < config.end:
            nxt = min(cur + timedelta(days=config.initial_chunk_days), config.end)
            for pair in process_range(client, cur, nxt, config.initial_chunk_days):
                yield pair
            cur = nxt


def compress_bins(pairs: Sequence[Tuple[datetime, int]], step: timedelta) -> List[Tuple[datetime, datetime, int, int]]:
    """Merge consecutive buckets into contiguous windows."""
    sorted_pairs = sorted(pairs, key=lambda row: row[0])
    windows: List[Tuple[datetime, datetime, int, int]] = []
    cur_start = cur_end = None
    bins_in = rows_in = 0

    for bucket_start, n in sorted_pairs:
        if cur_start is None:
            cur_start = bucket_start
            cur_end = bucket_start + step
            bins_in = 1
            rows_in = n
            continue
        if bucket_start == cur_end:
            cur_end += step
            bins_in += 1
            rows_in += n
        else:
            windows.append((cur_start, cur_end, bins_in, rows_in))
            cur_start = bucket_start
            cur_end = bucket_start + step
            bins_in = 1
            rows_in = n

    if cur_start is not None:
        windows.append((cur_start, cur_end, bins_in, rows_in))
    return windows


def format_windows(windows: Sequence[Tuple[datetime, datetime, int, int]], config: ScannerConfig) -> List[dict]:
    tz = config.tz
    formatted = []
    for start_utc, end_utc, bins_cnt, rows_cnt in windows:
        start_local = start_utc.astimezone(tz)
        end_local = end_utc.astimezone(tz)
        entry = {
            "key": build_key(start_utc, end_utc),
            "start_utc": start_utc.isoformat(),
            "end_utc": end_utc.isoformat(),
            "start_local": start_local.isoformat(),
            "end_local": end_local.isoformat(),
            "timezone": config.timezone_name,
            "bins": bins_cnt,
        }
        if config.include_counts:
            entry["row_count"] = rows_cnt
        formatted.append(entry)
    return formatted


def build_key(start_dt_utc: datetime, end_dt_utc: datetime) -> str:
    raw = f"{start_dt_utc.isoformat()}_{end_dt_utc.isoformat()}"
    return md5(raw.encode()).hexdigest()[:10]


if __name__ == "__main__":  # pragma: no cover
    import json
    import os

    cfg = ScannerConfig(
        host=os.getenv("INFLUX_HOST", "http://localhost:9000"),
        token=os.getenv("INFLUX_TOKEN", ""),
        database=os.getenv("INFLUX_DATABASE", "WFR25"),
        table=os.getenv("INFLUX_TABLE", "WFR25"),
    )
    print(json.dumps(scan_runs(cfg), indent=2))
