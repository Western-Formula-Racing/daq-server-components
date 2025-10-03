#!/usr/bin/env python3
"""
WFR DAQ System - Startup Data Loader
- Default: writes metrics in InfluxDB line protocol format to a Telegraf file
- BACKFILL=1: writes directly to InfluxDB (fast bulk load)
"""

import os
import sys
import asyncio
import time
import csv
import io
from datetime import datetime, timedelta, timezone
from typing import List, Optional, IO, Callable
from zoneinfo import ZoneInfo
from dataclasses import dataclass
import cantools
from influxdb_client import InfluxDBClient, WriteOptions

OUTPUT_FILE = "/var/lib/telegraf/can_metrics.out"

# InfluxDB direct write config
INFLUX_URL = "http://influxdb3:8181"
INFLUX_TOKEN = "apiv3_wfr_admin_token_change_in_production"
INFLUX_ORG = "WFR"
INFLUX_BUCKET = "WFR25"

# Mode switch
BACKFILL_MODE = os.getenv("BACKFILL", "0") == "1"


@dataclass
class ProgressStats:
    total_rows: int = 0
    processed_rows: int = 0
    failed_rows: int = 0
    start_time: float = 0


class CANLineProtocolWriter:
    def __init__(self, output_path: str, batch_size: int = 1000):
        self.batch_size = batch_size
        self.output_path = output_path
        self.org = "WFR"
        self.tz_toronto = ZoneInfo("America/Toronto")

        # Find DBC file in current directory
        dbc_files = [f for f in os.listdir(".") if f.endswith(".dbc")]
        if not dbc_files:
            raise FileNotFoundError("No DBC file found in container")

        self.db = cantools.database.load_file(dbc_files[0])
        print(f"📁 Loaded DBC file: {dbc_files[0]}")

        # Influx client setup (only if in backfill mode)
        if BACKFILL_MODE:
            self.client = InfluxDBClient(
                url=INFLUX_URL,
                token=INFLUX_TOKEN,
                org=INFLUX_ORG
            )
            self.write_api = self.client.write_api(
                write_options=WriteOptions(
                    batch_size=50000,
                    flush_interval=10_000,
                    jitter_interval=2000,
                    retry_interval=5000
                )
            )
        else:
            # Clear or create output file for Telegraf
            with open(self.output_path, "w") as f:
                pass
            self.client = None
            self.write_api = None

    def count_total_messages(self, file: IO[bytes], is_csv: bool = True) -> int:
        total = 0
        file.seek(0)
        if is_csv:
            text_stream = io.TextIOWrapper(file, encoding="utf-8", errors="replace", newline="")
            reader = csv.reader(text_stream)
            for row in reader:
                if len(row) < 11 or not row[0]:
                    continue
                try:
                    byte_values = [int(b) for b in row[3:11] if b]
                    if len(byte_values) != 8:
                        continue
                    msg_id = int(row[2])
                    self.db.get_message_by_frame_id(msg_id)
                    total += 1
                except Exception:
                    continue
            try:
                text_stream.detach()
            except:
                pass
        file.seek(0)
        return total

    def _escape_tag_value(self, val: str) -> str:
        return val.replace(" ", r"\ ").replace(",", r"\,").replace("=", r"\=")

    def _format_line_protocol(self, measurement: str, tags: dict, fields: dict, timestamp: int) -> str:
        tags_str = ",".join(f"{self._escape_tag_value(k)}={self._escape_tag_value(v)}" for k, v in tags.items())
        fields_str = ",".join(
            f"{self._escape_tag_value(k)}={v}" if isinstance(v, (int, float)) else f'{self._escape_tag_value(k)}="{v}"'
            for k, v in fields.items())
        line = f"{measurement},{tags_str} {fields_str} {timestamp}"
        return line

    def _parse_row(self, row: List[str], start_dt: datetime) -> Optional[List[str]]:
        try:
            if len(row) < 11 or not row[0]:
                return None

            relative_ms = int(row[0])
            msg_id = int(row[2])
            byte_values = [int(b) for b in row[3:11] if b]

            if len(byte_values) != 8:
                return None

            timestamp_dt = (start_dt + timedelta(milliseconds=relative_ms)).astimezone(timezone.utc)
            timestamp_ns = int(timestamp_dt.timestamp() * 1e9)

            message = self.db.get_message_by_frame_id(msg_id)
            decoded = message.decode(bytes(byte_values))

            lines = []
            for sig_name, raw_val in decoded.items():
                if hasattr(raw_val, 'value') and hasattr(raw_val, 'name'):
                    try:
                        val = float(raw_val.value)
                    except (ValueError, TypeError):
                        continue
                elif isinstance(raw_val, (int, float)):
                    val = float(raw_val)
                else:
                    continue

                tags = {
                    "signalName": sig_name,
                    "messageName": message.name,
                    "canId": str(msg_id),
                }
                fields = {"sensorReading": val}
                line = self._format_line_protocol("WFR25", tags, fields, timestamp_ns)
                lines.append(line)
            return lines
        except Exception:
            return None

    async def stream_csv(self, file: IO[bytes], csv_filename: str, on_progress: Optional[Callable[[int, int], None]] = None):
        total_rows = self.count_total_messages(file, is_csv=True)
        if on_progress:
            on_progress(0, total_rows)

        progress = ProgressStats(total_rows=total_rows, start_time=time.time())
        try:
            start_dt = datetime.strptime(csv_filename[:-4], "%Y-%m-%d-%H-%M-%S").replace(tzinfo=self.tz_toronto)
        except ValueError:
            print(f"⚠️  Warning: Could not parse datetime from filename {csv_filename}, using current time")
            start_dt = datetime.now(self.tz_toronto)

        file.seek(0)
        text_stream = io.TextIOWrapper(file, encoding="utf-8", errors="replace", newline="")
        reader = csv.reader(text_stream)

        batch_lines = []
        rows_in_batch = 0

        try:
            for row in reader:
                lines = self._parse_row(row, start_dt)
                if lines:
                    batch_lines.extend(lines)
                    rows_in_batch += 1

                    if len(batch_lines) >= self.batch_size:
                        if BACKFILL_MODE:
                            self.write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=batch_lines)
                        else:
                            with open(self.output_path, "a") as out_file:
                                out_file.write("\n".join(batch_lines) + "\n")

                        progress.processed_rows += rows_in_batch
                        if on_progress:
                            on_progress(progress.processed_rows, progress.total_rows)
                        batch_lines.clear()
                        rows_in_batch = 0

                        if not BACKFILL_MODE:
                            await asyncio.sleep(0.1)  # let Telegraf catch up

            if batch_lines:
                if BACKFILL_MODE:
                    self.write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=batch_lines)
                else:
                    with open(self.output_path, "a") as out_file:
                        out_file.write("\n".join(batch_lines) + "\n")
                progress.processed_rows += rows_in_batch
                if on_progress:
                    on_progress(progress.processed_rows, progress.total_rows)
        finally:
            try:
                text_stream.detach()
            except:
                pass

        elapsed = time.time() - progress.start_time
        mode_str = "InfluxDB Direct" if BACKFILL_MODE else "Telegraf File"
        print(f"\n✅ Processed {progress.processed_rows:,} rows in {elapsed:.2f}s using {mode_str} mode "
              f"({(progress.processed_rows/elapsed) if elapsed else 0:.1f} rows/s)")


def progress_callback(processed: int, total: int):
    if total > 0:
        percentage = (processed / total) * 100
        print(f"\r📊 Progress: {processed:,}/{total:,} rows ({percentage:.1f}%)", end="", flush=True)


async def load_startup_data():
    mode_str = "InfluxDB Direct (BACKFILL)" if BACKFILL_MODE else "Telegraf File"
    print(f"🚀 WFR DAQ System - Startup Data Loader [{mode_str}]")
    print("=" * 60)

    data_dir = "/data"
    if not os.path.exists(data_dir):
        print(f"❌ Data directory {data_dir} not found")
        return False

    csv_files = []
    for root, _, files in os.walk(data_dir):
        for file in files:
            if file.endswith(".csv"):
                csv_files.append(os.path.join(root, file))

    if not csv_files:
        print("⚠️ No CSV files found in /data directory or subdirectories")
        return True

    print(f"📂 Found {len(csv_files)} CSV file(s) to process:")
    for csv_file in csv_files:
        rel_path = os.path.relpath(csv_file, data_dir)
        print(f"   • {rel_path}")
    print()

    try:
        writer = CANLineProtocolWriter(output_path=OUTPUT_FILE, batch_size=1000)

        for i, csv_path in enumerate(csv_files, 1):
            csv_filename = os.path.basename(csv_path)
            rel_path = os.path.relpath(csv_path, data_dir)
            print(f"📊 Processing file {i}/{len(csv_files)}: {rel_path}")

            try:
                with open(csv_path, 'rb') as f:
                    await writer.stream_csv(
                        file=f,
                        csv_filename=csv_filename,
                        on_progress=progress_callback
                    )
                print(f"\n✅ Successfully wrote metrics for {rel_path}")
            except Exception as e:
                print(f"\n❌ Failed to process {rel_path}: {e}")
                continue

            print()

        print("🎉 Startup data writing completed!")
        return True

    except Exception as e:
        print(f"❌ Error initializing line protocol writer: {e}")
        return False


def main():
    start_time = time.time()
    try:
        success = asyncio.run(load_startup_data())
        elapsed = time.time() - start_time
        mode_str = "InfluxDB Direct" if BACKFILL_MODE else "Telegraf Loader"
        if success:
            print(f"\n🏁 Data writing completed in {elapsed:.2f} seconds ({mode_str})")
            sys.exit(0)
        else:
            print(f"\n💥 Data writing failed after {elapsed:.2f} seconds ({mode_str})")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n⏹️ Data writing interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()