#!/usr/bin/env python3
"""
WFR DAQ System - Startup Data Loader (Docker Version)
Loads CSV data into InfluxDB during system initialization
"""

import os
import sys
import asyncio
import time
import zipfile
import csv
import io
from datetime import datetime, timedelta, timezone
from typing import List, Optional, IO, Callable
from zoneinfo import ZoneInfo
from dataclasses import dataclass
import cantools
from influxdb_client.client.influxdb_client import InfluxDBClient
from influxdb_client.client.write.point import Point
from influxdb_client.client.write_api import WriteOptions

@dataclass
class ProgressStats:
    total_rows: int = 0
    processed_rows: int = 0
    failed_rows: int = 0
    start_time: float = 0

class CANInfluxStreamer:
    def __init__(self, bucket: str, batch_size: int = 1000, max_concurrent_uploads: int = 10):
        self.batch_size = batch_size
        self.max_concurrent_uploads = max_concurrent_uploads
        self.bucket = bucket
        self.org = "WFR"
        self.tz_toronto = ZoneInfo("America/Toronto")
        
        # Use Docker internal network URL
        self.url = "http://influxdb2:8086"
        
        # Find DBC file in current directory
        dbc_files = [f for f in os.listdir(".") if f.endswith(".dbc")]
        if not dbc_files:
            raise FileNotFoundError("No DBC file found in container")
        
        self.db = cantools.database.load_file(dbc_files[0])
        print(f"📁 Loaded DBC file: {dbc_files[0]}")
        
        self.client = InfluxDBClient(
            url=self.url, 
            token=os.getenv("INFLUXDB_TOKEN"), 
            org=self.org, 
            enable_gzip=True
        )
        self.write_api = self.client.write_api(
            write_options=WriteOptions(batch_size=batch_size, flush_interval=10_000)
        )

    def count_total_messages(self, file: IO[bytes], is_csv: bool = True) -> int:
        """Count total valid CAN messages in the file"""
        total = 0
        file.seek(0)
        
        if is_csv:
            text_stream = io.TextIOWrapper(file, encoding="utf-8", errors="replace", newline="")
            reader = csv.reader(text_stream)
            
            for row in reader:
                if len(row) < 11 or not row[0]:
                    continue
                try:
                    # Check if we have valid byte values
                    byte_values = [int(b) for b in row[3:11] if b]
                    if len(byte_values) != 8:
                        continue
                    
                    # Check if message ID exists in DBC
                    msg_id = int(row[2])
                    self.db.get_message_by_frame_id(msg_id)
                    total += 1
                except Exception:
                    continue
            
            # Detach to prevent closing the underlying stream
            try:
                text_stream.detach()
            except:
                pass
        
        file.seek(0)
        return total

    def _parse_row(self, row: List[str], start_dt: datetime, filename: str) -> Optional[List[Point]]:
        """Parse a single CSV row into InfluxDB points"""
        try:
            if len(row) < 11 or not row[0]:
                return None

            relative_ms = int(row[0])
            msg_id = int(row[2])
            byte_values = [int(b) for b in row[3:11] if b]
            
            if len(byte_values) != 8:
                return None

            timestamp = (start_dt + timedelta(milliseconds=relative_ms)).astimezone(timezone.utc)
            message = self.db.get_message_by_frame_id(msg_id)
            decoded = message.decode(bytes(byte_values))

            points = []
            for sig_name, raw in decoded.items():
                sig = message.get_signal_by_name(sig_name)
                unit = getattr(sig, "unit", "N/A")
                desc = getattr(sig, "comment", "") or "No description"
                
                val = float(raw.value) if hasattr(raw, "value") else float(raw)
                label = raw.name if hasattr(raw, "name") else str(raw)

                pt = (
                    Point("canBus")
                    .tag("signalName", sig_name)
                    .tag("messageName", message.name)
                    .tag("canId", str(msg_id))
                    .field("sensorReading", val)
                    .field("unit", unit)
                    .field("description", desc)
                    .field("signalLabel", label)
                    .time(timestamp)
                )
                points.append(pt)
            return points

        except Exception as e:
            return None

    async def stream_csv_to_influx(self, file: IO[bytes], csv_filename: str, on_progress: Optional[Callable[[int, int], None]] = None):
        """Stream CSV data to InfluxDB"""
        total_rows = self.count_total_messages(file, is_csv=True)
        if on_progress:
            on_progress(0, total_rows)
        
        progress = ProgressStats(total_rows=total_rows, start_time=time.time())
        
        # Parse filename for start datetime
        try:
            start_dt = datetime.strptime(csv_filename[:-4], "%Y-%m-%d-%H-%M-%S").replace(tzinfo=self.tz_toronto)
        except ValueError:
            print(f"⚠️  Warning: Could not parse datetime from filename {csv_filename}, using current time")
            start_dt = datetime.now(self.tz_toronto)
        
        file.seek(0)
        text_stream = io.TextIOWrapper(file, encoding="utf-8", errors="replace", newline="")
        reader = csv.reader(text_stream)
        
        batch = []
        rows_in_batch = 0
        
        try:
            for row in reader:
                points = self._parse_row(row, start_dt, csv_filename)
                if points:
                    batch.extend(points)
                    rows_in_batch += 1
                    
                    if len(batch) >= self.batch_size:
                        # Write batch to InfluxDB
                        try:
                            self.write_api.write(bucket=self.bucket, org=self.org, record=batch)
                            progress.processed_rows += rows_in_batch
                            if on_progress:
                                on_progress(progress.processed_rows, progress.total_rows)
                        except Exception as e:
                            progress.failed_rows += rows_in_batch
                            print(f"❌ Failed to write batch: {e}")
                        
                        batch.clear()
                        rows_in_batch = 0
            
            # Write remaining batch
            if batch:
                try:
                    self.write_api.write(bucket=self.bucket, org=self.org, record=batch)
                    progress.processed_rows += rows_in_batch
                    if on_progress:
                        on_progress(progress.processed_rows, progress.total_rows)
                except Exception as e:
                    progress.failed_rows += rows_in_batch
                    print(f"❌ Failed to write final batch: {e}")
        
        finally:
            try:
                text_stream.detach()
            except:
                pass
        
        elapsed = time.time() - progress.start_time
        print(f"\n✅ Processed {progress.processed_rows:,} rows in {elapsed:.2f}s ({(progress.processed_rows/elapsed) if elapsed else 0:.1f} rows/s)")

    def close(self):
        """Close InfluxDB connections"""
        try:
            self.write_api.flush()
        except Exception as e:
            print(f"⚠️ Error during flush: {e}")
        finally:
            try:
                self.write_api.close()
            except Exception as e:
                print(f"⚠️ Error closing write_api: {e}")
            try:
                self.client.close()
            except Exception as e:
                print(f"⚠️ Error closing client: {e}")

def progress_callback(processed: int, total: int):
    """Progress callback for data upload"""
    if total > 0:
        percentage = (processed / total) * 100
        print(f"\r📊 Progress: {processed:,}/{total:,} rows ({percentage:.1f}%)", end="", flush=True)

async def load_startup_data():
    """Load all CSV data from /data directory into InfluxDB"""
    print("🚀 WFR DAQ System - Startup Data Loader (Docker)")
    print("=" * 55)
    
    # Check for InfluxDB token
    if not os.getenv("INFLUXDB_TOKEN"):
        print("❌ No InfluxDB token found in environment")
        print("Make sure INFLUXDB_TOKEN is passed to the container")
        return False
    
    # Wait for InfluxDB to be ready
    print("⏳ Waiting for InfluxDB to be ready...")
    max_retries = 30
    for i in range(max_retries):
        try:
            # Test connection
            client = InfluxDBClient(url="http://influxdb2:8086", token=os.getenv("INFLUXDB_TOKEN"), org="WFR")
            client.ping()
            client.close()
            print("✅ InfluxDB is ready!")
            break
        except Exception as e:
            if i == max_retries - 1:
                print(f"❌ InfluxDB not ready after {max_retries} attempts: {e}")
                return False
            time.sleep(2)
    
    # Find CSV files in /data directory (including subdirectories)
    data_dir = "/data"
    if not os.path.exists(data_dir):
        print(f"❌ Data directory {data_dir} not found")
        return False
    
    csv_files = []
    for root, dirs, files in os.walk(data_dir):
        for file in files:
            if file.endswith(".csv"):
                csv_files.append(os.path.join(root, file))
    
    if not csv_files:
        print("⚠️ No CSV files found in /data directory or subdirectories")
        return True  # Not an error, just no data to load
    
    print(f"📂 Found {len(csv_files)} CSV file(s) to process:")
    for csv_file in csv_files:
        # Show relative path from data directory for cleaner output
        rel_path = os.path.relpath(csv_file, data_dir)
        print(f"   • {rel_path}")
    print()
    
    # Initialize streamer
    try:
        streamer = CANInfluxStreamer(bucket="ourCar", batch_size=5000, max_concurrent_uploads=1)
        
        for i, csv_path in enumerate(csv_files, 1):
            csv_filename = os.path.basename(csv_path)
            rel_path = os.path.relpath(csv_path, data_dir)
            print(f"📊 Processing file {i}/{len(csv_files)}: {rel_path}")
            
            try:
                with open(csv_path, 'rb') as f:
                    await streamer.stream_csv_to_influx(
                        file=f,
                        csv_filename=csv_filename,
                        on_progress=progress_callback
                    )
                print(f"\n✅ Successfully loaded {rel_path}")
                
            except Exception as e:
                print(f"\n❌ Failed to process {rel_path}: {e}")
                continue
            
            print()  # Add spacing between files
        
        print("🎉 Startup data loading completed!")
        return True
        
    except Exception as e:
        print(f"❌ Error initializing data streamer: {e}")
        return False
    finally:
        try:
            streamer.close()
        except:
            pass

def main():
    """Main entry point"""
    start_time = time.time()
    
    try:
        success = asyncio.run(load_startup_data())
        elapsed = time.time() - start_time
        
        if success:
            print(f"\n🏁 Data loading completed in {elapsed:.2f} seconds")
            sys.exit(0)
        else:
            print(f"\n💥 Data loading failed after {elapsed:.2f} seconds")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n⏹️ Data loading interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
