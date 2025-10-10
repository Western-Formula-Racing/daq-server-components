#!/usr/bin/env python3
"""
WFR DAQ System - Startup Data Loader
- Default: writes metrics in InfluxDB line protocol format to a Telegraf file
- BACKFILL=1: writes directly to InfluxDB (fast bulk load)
- Progress tracking with resume capability
- Memory-efficient streaming
"""

import os
import sys
import asyncio
import time
import csv
import io
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, IO, Callable, Dict
from zoneinfo import ZoneInfo
from dataclasses import dataclass, asdict
import cantools
from influxdb_client import InfluxDBClient, WriteOptions

OUTPUT_FILE = "/var/lib/telegraf/can_metrics.out"
PROGRESS_FILE = "/var/lib/telegraf/can_progress.json"

# InfluxDB direct write config
INFLUX_URL = "http://influxdb3:8181"
INFLUX_TOKEN = "apiv3_wfr_admin_token_change_in_production"
INFLUX_ORG = "WFR"
INFLUX_BUCKET = "WFR25"

# Mode switch
BACKFILL_MODE = os.getenv("BACKFILL", "0") == "1"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


@dataclass
class FileProgress:
    filename: str
    total_rows: int = 0
    processed_rows: int = 0
    completed: bool = False
    last_updated: float = 0


@dataclass
class ProgressState:
    files: Dict[str, FileProgress]
    last_saved: float = 0

    def to_dict(self):
        return {
            "files": {k: asdict(v) for k, v in self.files.items()},
            "last_saved": self.last_saved
        }

    @classmethod
    def from_dict(cls, data: dict):
        files = {k: FileProgress(**v) for k, v in data.get("files", {}).items()}
        return cls(files=files, last_saved=data.get("last_saved", 0))


class ProgressManager:
    def __init__(self, progress_file: str):
        self.progress_file = progress_file
        self.state: Optional[ProgressState] = None
        self.load()

    def load(self):
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, 'r') as f:
                    data = json.load(f)
                    self.state = ProgressState.from_dict(data)
                    logger.info(f"Loaded progress state with {len(self.state.files)} files")
            except Exception as e:
                logger.warning(f"Could not load progress file: {e}, starting fresh")
                self.state = ProgressState(files={})
        else:
            self.state = ProgressState(files={})

    def save(self):
        try:
            self.state.last_saved = time.time()
            with open(self.progress_file, 'w') as f:
                json.dump(self.state.to_dict(), f, indent=2)
        except Exception as e:
            logger.error(f"Could not save progress: {e}")

    def get_file_progress(self, filename: str) -> FileProgress:
        if filename not in self.state.files:
            self.state.files[filename] = FileProgress(filename=filename)
        return self.state.files[filename]

    def is_file_completed(self, filename: str) -> bool:
        return filename in self.state.files and self.state.files[filename].completed

    def mark_completed(self, filename: str):
        if filename in self.state.files:
            self.state.files[filename].completed = True
            self.save()


class CANLineProtocolWriter:
    def __init__(self, output_path: str, batch_size: int = 5000):
        self.batch_size = batch_size
        self.output_path = output_path
        self.org = "WFR"
        self.tz_toronto = ZoneInfo("America/Toronto")
        
        # Memory tracking
        self._message_cache = {}  # Cache message objects
        self._last_memory_clear = time.time()

        # Find DBC file in current directory
        dbc_files = [f for f in os.listdir(".") if f.endswith(".dbc")]
        if not dbc_files:
            raise FileNotFoundError("No DBC file found in container")

        self.db = cantools.database.load_file(dbc_files[0])
        logger.info(f"Loaded DBC file: {dbc_files[0]}")

        # Cache all message IDs for faster lookup
        for msg in self.db.messages:
            self._message_cache[msg.frame_id] = msg

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

    def _get_message(self, msg_id: int):
        """Get message from cache or database"""
        if msg_id not in self._message_cache:
            self._message_cache[msg_id] = self.db.get_message_by_frame_id(msg_id)
        return self._message_cache[msg_id]

    def count_valid_messages(self, file: IO[bytes]) -> int:
        """Quick count of valid messages without full parsing"""
        count = 0
        file.seek(0)
        text_stream = io.TextIOWrapper(file, encoding="utf-8", errors="replace", newline="")
        reader = csv.reader(text_stream)
        
        for row in reader:
            if len(row) >= 11 and row[0] and row[2]:
                try:
                    msg_id = int(row[2])
                    if msg_id in self._message_cache:
                        byte_values = [int(b) for b in row[3:11] if b]
                        if len(byte_values) == 8:
                            count += 1
                except (ValueError, IndexError):
                    continue
        
        text_stream.detach()
        file.seek(0)
        return count

    def _escape_tag_value(self, val: str) -> str:
        return val.replace(" ", r"\ ").replace(",", r"\,").replace("=", r"\=")

    def _format_line_protocol(self, measurement: str, tags: dict, fields: dict, timestamp: int) -> str:
        tags_str = ",".join(f"{self._escape_tag_value(k)}={self._escape_tag_value(v)}" for k, v in tags.items())
        fields_str = ",".join(
            f"{self._escape_tag_value(k)}={v}" if isinstance(v, (int, float)) else f'{self._escape_tag_value(k)}="{v}"'
            for k, v in fields.items())
        return f"{measurement},{tags_str} {fields_str} {timestamp}"

    def _parse_row(self, row: List[str], start_dt: datetime) -> Optional[List[str]]:
        try:
            if len(row) < 11 or not row[0]:
                return None

            relative_ms = int(row[0])
            msg_id = int(row[2])
            byte_values = [int(b) for b in row[3:11] if b]

            if len(byte_values) != 8 or msg_id not in self._message_cache:
                return None

            timestamp_dt = (start_dt + timedelta(milliseconds=relative_ms)).astimezone(timezone.utc)
            timestamp_ns = int(timestamp_dt.timestamp() * 1e9)

            message = self._message_cache[msg_id]
            decoded = message.decode(bytes(byte_values))

            lines = []
            for sig_name, raw_val in decoded.items():
                if hasattr(raw_val, 'value'):
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

    async def stream_csv(self, file: IO[bytes], csv_filename: str, 
                        progress_mgr: ProgressManager,
                        on_progress: Optional[Callable[[int, int], None]] = None):
        
        file_progress = progress_mgr.get_file_progress(csv_filename)
        
        # Count total if not already done
        if file_progress.total_rows == 0:
            logger.info(f"Counting messages in {csv_filename}...")
            file_progress.total_rows = self.count_valid_messages(file)
            progress_mgr.save()
            logger.info(f"Found {file_progress.total_rows:,} valid messages")

        if on_progress:
            on_progress(file_progress.processed_rows, file_progress.total_rows)

        start_time = time.time()
        try:
            start_dt = datetime.strptime(csv_filename[:-4], "%Y-%m-%d-%H-%M-%S").replace(tzinfo=self.tz_toronto)
        except ValueError:
            logger.warning(f"Could not parse datetime from {csv_filename}, using current time")
            start_dt = datetime.now(self.tz_toronto)

        file.seek(0)
        text_stream = io.TextIOWrapper(file, encoding="utf-8", errors="replace", newline="")
        reader = csv.reader(text_stream)

        batch_lines = []
        rows_in_batch = 0
        last_save = time.time()

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

                        file_progress.processed_rows += rows_in_batch
                        
                        # Save progress every 30 seconds
                        if time.time() - last_save > 30:
                            progress_mgr.save()
                            last_save = time.time()
                        
                        if on_progress:
                            on_progress(file_progress.processed_rows, file_progress.total_rows)
                        
                        # Clear batch to free memory
                        batch_lines.clear()
                        rows_in_batch = 0

                        if not BACKFILL_MODE:
                            await asyncio.sleep(0.05)

            # Write remaining batch
            if batch_lines:
                if BACKFILL_MODE:
                    self.write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=batch_lines)
                else:
                    with open(self.output_path, "a") as out_file:
                        out_file.write("\n".join(batch_lines) + "\n")
                file_progress.processed_rows += rows_in_batch
                if on_progress:
                    on_progress(file_progress.processed_rows, file_progress.total_rows)
                batch_lines.clear()
        finally:
            text_stream.detach()

        elapsed = time.time() - start_time
        rate = file_progress.processed_rows / elapsed if elapsed > 0 else 0
        logger.info(f"Processed {file_progress.processed_rows:,} rows in {elapsed:.2f}s ({rate:.0f} rows/s)")


def progress_callback(processed: int, total: int):
    if total > 0:
        percentage = (processed / total) * 100
        # print(f"\rProgress: {processed:,}/{total:,} ({percentage:.1f}%)", end="", flush=True)
        logger.info(f"Progress: {processed:,}/{total:,} ({percentage:.1f}%)")


async def load_startup_data():
    mode_str = "InfluxDB Direct" if BACKFILL_MODE else "Telegraf File"
    logger.info(f"WFR DAQ System - Startup Data Loader [{mode_str}]")
    logger.info("=" * 60)

    data_dir = "/data"
    if not os.path.exists(data_dir):
        logger.error(f"Data directory {data_dir} not found")
        return False

    csv_files = []
    for root, _, files in os.walk(data_dir):
        for file in files:
            if file.endswith(".csv"):
                csv_files.append(os.path.join(root, file))

    if not csv_files:
        logger.warning("No CSV files found in /data directory")
        return True

    logger.info(f"Found {len(csv_files)} CSV file(s)")

    try:
        writer = CANLineProtocolWriter(output_path=OUTPUT_FILE, batch_size=5000)
        progress_mgr = ProgressManager(PROGRESS_FILE)

        files_to_process = []
        for csv_path in csv_files:
            csv_filename = os.path.basename(csv_path)
            if not progress_mgr.is_file_completed(csv_filename):
                files_to_process.append(csv_path)
            else:
                logger.info(f"Skipping completed file: {csv_filename}")

        if not files_to_process:
            logger.info("All files already processed!")
            return True

        logger.info(f"Processing {len(files_to_process)} file(s)")

        for i, csv_path in enumerate(files_to_process, 1):
            csv_filename = os.path.basename(csv_path)
            logger.info(f"\nFile {i}/{len(files_to_process)}: {csv_filename}")

            try:
                with open(csv_path, 'rb') as f:
                    await writer.stream_csv(
                        file=f,
                        csv_filename=csv_filename,
                        progress_mgr=progress_mgr,
                        on_progress=progress_callback
                    )
                print()  # New line after progress
                progress_mgr.mark_completed(csv_filename)
                logger.info(f"Completed: {csv_filename}")
            except Exception as e:
                logger.error(f"Failed to process {csv_filename}: {e}")
                continue

        logger.info("\nData writing completed!")
        return True

    except Exception as e:
        logger.error(f"Error initializing writer: {e}")
        return False


def main():
    start_time = time.time()
    try:
        success = asyncio.run(load_startup_data())
        elapsed = time.time() - start_time
        if success:
            logger.info(f"Completed in {elapsed:.2f} seconds")
            sys.exit(0)
        else:
            logger.error(f"Failed after {elapsed:.2f} seconds")
            sys.exit(1)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()