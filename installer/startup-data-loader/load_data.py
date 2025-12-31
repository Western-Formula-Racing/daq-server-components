#!/usr/bin/env python3

# To run locally: python3 load_data.py
# Options

# Add 5 second pause between files
# INTER_FILE_DELAY=5 python3 load_data.py

# Combine both delays
# BATCH_DELAY=0.05 INTER_FILE_DELAY=10 python3 load_data.py

"""
WFR DAQ System - Startup Data Loader
- Writes metrics directly to InfluxDB (fast bulk load)
- Supports resume after interrupt using JSON state file
"""

import os
import sys
import asyncio
import time
import csv
import io
import json
import hashlib
from datetime import datetime, timedelta, timezone
from typing import List, Optional, IO, Callable, Dict, Set
from zoneinfo import ZoneInfo
from dataclasses import dataclass, asdict
from pathlib import Path
import cantools
from influxdb_client import InfluxDBClient, WriteOptions


def _env_int(var_name: str, default: int) -> int:
    """Parse an integer environment variable with a fallback."""
    raw_value = os.getenv(var_name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError:
        print(f"⚠️  Invalid value '{raw_value}' for {var_name}; using default {default}")
        return default

# Progress state file
PROGRESS_FILE = "load_data_progress.json"
# InfluxDB direct write config
INFLUX_URL = os.getenv("INFLUXDB_URL", "http://influxdb3:8181")
INFLUX_TOKEN = os.getenv("INFLUXDB_TOKEN", "apiv3_dev-influxdb-admin-token")
INFLUX_ORG = "WFR"
INFLUX_BUCKET = "WFR25"
DBC_ENV_VAR = "DBC_FILE_PATH"
DBC_FILENAME = "example.dbc"
INSTALLER_ROOT = Path(__file__).resolve().parent.parent

# Performance tuning - delays to reduce server load
BATCH_DELAY = float(os.getenv("BATCH_DELAY", "0.0"))  # Delay after each batch write (seconds)
INTER_FILE_DELAY = float(os.getenv("INTER_FILE_DELAY", "0.0"))  # Delay between files (seconds)
CSV_RESTART_INTERVAL = _env_int("CSV_RESTART_INTERVAL", 10)  # Restart loader after N CSV files


@dataclass
class FileProgress:
    filename: str
    file_hash: str
    total_rows: int
    processed_rows: int
    completed: bool = False
    last_update: float = 0


@dataclass
class ProgressState:
    completed_files: Set[str]
    file_progress: Dict[str, FileProgress]
    start_time: float
    last_saved: float

    def to_dict(self):
        return {
            'completed_files': list(self.completed_files),
            'file_progress': {k: asdict(v) for k, v in self.file_progress.items()},
            'start_time': self.start_time,
            'last_saved': self.last_saved
        }

    @classmethod
    def from_dict(cls, data: dict):
        file_progress = {}
        for k, v in data.get('file_progress', {}).items():
            file_progress[k] = FileProgress(**v)
        
        return cls(
            completed_files=set(data.get('completed_files', [])),
            file_progress=file_progress,
            start_time=data.get('start_time', time.time()),
            last_saved=data.get('last_saved', time.time())
        )

    @classmethod
    def load(cls, filepath: str = PROGRESS_FILE):
        """Load progress state from JSON file"""
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
                    state = cls.from_dict(data)
                    print(f"📥 Loaded progress state: {len(state.completed_files)} files completed")
                    return state
            except Exception as e:
                print(f"⚠️  Could not load progress file: {e}. Starting fresh.")
        
        return cls(
            completed_files=set(),
            file_progress={},
            start_time=time.time(),
            last_saved=time.time()
        )

    def save(self, filepath: str = PROGRESS_FILE):
        """Save progress state to JSON file"""
        self.last_saved = time.time()
        try:
            with open(filepath, 'w') as f:
                json.dump(self.to_dict(), f, indent=2)
        except Exception as e:
            print(f"⚠️  Could not save progress: {e}")

    def should_process_file(self, file_path: str, file_hash: str) -> bool:
        """Check if a file needs to be processed"""
        if file_path in self.completed_files:
            return False
        
        if file_path in self.file_progress:
            progress = self.file_progress[file_path]
            # Only skip if hash matches and file was completed
            if progress.file_hash == file_hash and progress.completed:
                return False
        
        return True

    def get_file_offset(self, file_path: str, file_hash: str) -> int:
        """Get the row offset to resume from for a file"""
        if file_path in self.file_progress:
            progress = self.file_progress[file_path]
            # Only resume if hash matches
            if progress.file_hash == file_hash and not progress.completed:
                return progress.processed_rows
        return 0


def compute_file_hash(file_path: str) -> str:
    """Compute MD5 hash of file for change detection"""
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            # Read first and last 64KB to speed up hash for large files
            chunk = f.read(65536)
            hash_md5.update(chunk)
            f.seek(-min(65536, os.path.getsize(file_path)), 2)
            chunk = f.read(65536)
            hash_md5.update(chunk)
    except Exception:
        return ""
    return hash_md5.hexdigest()


def _resolve_dbc_path() -> Path:
    """Resolve the DBC path using env override, shared installer copy or local fallback."""
    env_override = os.getenv(DBC_ENV_VAR)
    if env_override:
        env_path = Path(env_override).expanduser()
        if env_path.exists():
            return env_path
        print(f"⚠️  {DBC_ENV_VAR}={env_override} not found; falling back to default lookup.")

    shared_candidates = [
        INSTALLER_ROOT / DBC_FILENAME,
        Path("/installer") / DBC_FILENAME,
    ]
    for candidate in shared_candidates:
        if candidate.exists():
            return candidate

    # Final fallback: look for local .dbc files (maintains backwards compatibility)
    current_dir = Path(__file__).resolve().parent
    dbc_candidates = sorted(
        current_dir.glob("*.dbc"),
        key=lambda file_path: file_path.stat().st_mtime,
        reverse=True
    )
    if dbc_candidates:
        return dbc_candidates[0]

    raise FileNotFoundError(
        f"Could not locate {DBC_FILENAME}. Place it in the installer root "
        f"or set {DBC_ENV_VAR} to the desired path."
    )


class CANLineProtocolWriter:
    def __init__(self, batch_size: int = 1000, progress_state: Optional[ProgressState] = None):
        self.batch_size = batch_size
        self.org = "WFR"
        self.tz_toronto = ZoneInfo("America/Toronto")
        self.progress_state = progress_state or ProgressState.load()

        # Load the shared DBC file
        dbc_path = _resolve_dbc_path()
        self.db = cantools.database.load_file(str(dbc_path))
        print(f"📁 Loaded DBC file: {dbc_path}")

        # Influx client setup
        # Adjust batch size and flush interval based on BATCH_DELAY
        influx_batch_size = 50000
        influx_flush_interval = 10_000
        
        if BATCH_DELAY > 0:
            # If user adds delay, we can use larger batches
            influx_batch_size = min(100000, int(50000 * (1 + BATCH_DELAY)))
            influx_flush_interval = min(30_000, int(10_000 * (1 + BATCH_DELAY)))
        
        self.client = InfluxDBClient(
            url=INFLUX_URL,
            token=INFLUX_TOKEN,
            org=INFLUX_ORG
        )
        self.write_api = self.client.write_api(
            write_options=WriteOptions(
                batch_size=influx_batch_size,
                flush_interval=influx_flush_interval,
                jitter_interval=2000,
                retry_interval=5000
            )
        )
        print(f"⚙️  InfluxDB batch_size={influx_batch_size}, flush_interval={influx_flush_interval}ms")


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

    async def stream_csv(self, file: IO[bytes], csv_path: str, csv_filename: str, 
                        on_progress: Optional[Callable[[int, int], None]] = None):
        file_hash = compute_file_hash(csv_path)
        resume_offset = self.progress_state.get_file_offset(csv_path, file_hash)
        
        total_rows = self.count_total_messages(file, is_csv=True)
        
        # Initialize or update file progress
        if csv_path not in self.progress_state.file_progress:
            self.progress_state.file_progress[csv_path] = FileProgress(
                filename=csv_filename,
                file_hash=file_hash,
                total_rows=total_rows,
                processed_rows=0
            )
        
        file_progress = self.progress_state.file_progress[csv_path]
        
        if resume_offset > 0:
            print(f"🔄 Resuming from row {resume_offset:,}/{total_rows:,}")
        
        if on_progress:
            on_progress(resume_offset, total_rows)

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
        current_row = 0
        save_interval = 1000  # Save progress every 1000 rows

        try:
            for row in reader:
                # Skip rows if resuming
                if current_row < resume_offset:
                    current_row += 1
                    continue
                
                lines = self._parse_row(row, start_dt)
                if lines:
                    batch_lines.extend(lines)
                    rows_in_batch += 1
                    current_row += 1

                    if len(batch_lines) >= self.batch_size:
                        self.write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=batch_lines)

                        file_progress.processed_rows = current_row
                        file_progress.last_update = time.time()
                        
                        if on_progress:
                            on_progress(current_row, total_rows)
                        
                        # Save progress periodically
                        if current_row % save_interval == 0:
                            self.progress_state.save()
                        
                        batch_lines.clear()
                        rows_in_batch = 0

                        # Configurable delay to reduce server load
                        if BATCH_DELAY > 0:
                            await asyncio.sleep(BATCH_DELAY)

            # Write remaining batch
            if batch_lines:
                self.write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=batch_lines)
                file_progress.processed_rows = current_row
                if on_progress:
                    on_progress(current_row, total_rows)
            
            # Mark file as completed
            file_progress.completed = True
            file_progress.last_update = time.time()
            self.progress_state.completed_files.add(csv_path)
            self.progress_state.save()
            
        finally:
            try:
                text_stream.detach()
            except:
                pass

        print(f"\n✅ Processed {current_row:,} rows using InfluxDB Direct mode")


def make_progress_callback(file_path: str):
    """Create a progress callback for a specific file"""
    def callback(processed: int, total: int):
        if total > 0:
            percentage = (processed / total) * 100
            rel_path = os.path.basename(file_path)
            print(f"\r📊 {rel_path}: {processed:,}/{total:,} rows ({percentage:.1f}%)", end="", flush=True)
    return callback


async def load_startup_data():
    print(f"🚀 WFR DAQ System - Startup Data Loader [InfluxDB Direct]")
    print("=" * 60)
    restart_interval = CSV_RESTART_INTERVAL if CSV_RESTART_INTERVAL > 0 else None
    if restart_interval:
        print(f"♻️  Loader will restart after every {restart_interval} CSV file(s)")

    # Load or initialize progress state
    progress_state = ProgressState.load()
    files_processed_since_restart = 0

    # Check for local data directory first, then container path
    data_dir = "data" if os.path.exists("data") else "/data"
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

    # Filter out already completed files
    files_to_process = []
    for csv_path in csv_files:
        file_hash = compute_file_hash(csv_path)
        if progress_state.should_process_file(csv_path, file_hash):
            files_to_process.append(csv_path)
        else:
            rel_path = os.path.relpath(csv_path, data_dir)
            print(f"⏭️  Skipping already completed: {rel_path}")

    if not files_to_process:
        print("✅ All files already processed!")
        return True

    print(f"📂 Found {len(files_to_process)} CSV file(s) to process:")
    for csv_file in files_to_process:
        rel_path = os.path.relpath(csv_file, data_dir)
        print(f"   • {rel_path}")
    print()

    try:
        writer = CANLineProtocolWriter(
            batch_size=1000,
            progress_state=progress_state
        )

        for i, csv_path in enumerate(files_to_process, 1):
            csv_filename = os.path.basename(csv_path)
            rel_path = os.path.relpath(csv_path, data_dir)
            print(f"📊 Processing file {i}/{len(files_to_process)}: {rel_path}")

            file_processed = False
            try:
                with open(csv_path, 'rb') as f:
                    await writer.stream_csv(
                        file=f,
                        csv_path=csv_path,
                        csv_filename=csv_filename,
                        on_progress=make_progress_callback(csv_path)
                    )
                print(f"\n✅ Successfully wrote metrics for {rel_path}")
                file_processed = True
            except Exception as e:
                print(f"\n❌ Failed to process {rel_path}: {e}")
                # Save progress even on error
                progress_state.save()
                continue

            if file_processed:
                files_processed_since_restart += 1

            # Delay between files to reduce server load
            if INTER_FILE_DELAY > 0 and i < len(files_to_process):
                print(f"⏸️  Waiting {INTER_FILE_DELAY}s before next file...")
                await asyncio.sleep(INTER_FILE_DELAY)
            
            print()

            should_restart = (
                restart_interval is not None
                and files_processed_since_restart >= restart_interval
                and i < len(files_to_process)
            )
            if should_restart:
                remaining = len(files_to_process) - i
                print(f"♻️  Processed {files_processed_since_restart} CSV file(s); restarting to continue with {remaining} remaining...")
                progress_state.save()
                sys.stdout.flush()
                os.execv(sys.executable, [sys.executable] + sys.argv)

        print("🎉 Startup data writing completed!")
        
        # Clean up progress file on successful completion
        if os.path.exists(PROGRESS_FILE):
            os.remove(PROGRESS_FILE)
            print(f"🧹 Cleaned up progress file")
        
        return True

    except Exception as e:
        print(f"❌ Error initializing line protocol writer: {e}")
        progress_state.save()
        return False


def main():
    start_time = time.time()
    try:
        success = asyncio.run(load_startup_data())
        elapsed = time.time() - start_time
        if success:
            print(f"\n🏁 Data writing completed in {elapsed:.2f} seconds (InfluxDB Direct)")
            sys.exit(0)
        else:
            print(f"\n💥 Data writing failed after {elapsed:.2f} seconds (InfluxDB Direct)")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n⏹️ Data writing interrupted - progress saved")
        print(f"💡 Run the script again to resume from where it left off")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()