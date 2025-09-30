import zipfile, csv, io, time, asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Optional, IO, Callable, Generator
from zoneinfo import ZoneInfo
from dataclasses import dataclass
import cantools
from influxdb_client.client.influxdb_client import InfluxDBClient
from influxdb_client.client.write.point import Point
from influxdb_client.client.write_api import WriteOptions, ASYNCHRONOUS
import os

if os.getenv("DEBUG") is None:
    from dotenv import load_dotenv

    load_dotenv()

@dataclass
class ProgressStats:
    total_rows: int = 0
    processed_rows: int = 0
    failed_rows: int = 0
    start_time: float = 0
    pending_writes: int = 0  # Track async operations in flight


class CANInfluxStreamer:

    def __init__(
        self, bucket: str, batch_size: int = 500, max_concurrent_uploads: int = 5,
        enable_progress_counting: bool = True, max_queue_size: int = 100,
        rate_limit_delay: float = 0.01, max_retries: int = 3, 
        adaptive_backoff: bool = True
    ):

        self.batch_size = batch_size
        self.max_concurrent_uploads = max_concurrent_uploads
        self.bucket = bucket
        self.enable_progress_counting = enable_progress_counting
        self.max_queue_size = max_queue_size
        self.rate_limit_delay = rate_limit_delay
        self.max_retries = max_retries
        self.adaptive_backoff = adaptive_backoff
        self._consecutive_failures = 0
        self._last_error_time = 0
        self._write_callbacks = {}  # Track async write callbacks
        self._callback_lock = asyncio.Lock()
        self.org = "WFR"
        self.tz_toronto = ZoneInfo("America/Toronto")
        self.url = (
            "http://influxdb2:8086"
        )

        # finding dbc file in the current directory
        self.db = cantools.database.load_file(
            [
                file
                for file in os.listdir(os.path.dirname(os.path.abspath(__file__)))
                if file.endswith(".dbc")
            ][0]
        )

        self.client = InfluxDBClient(
            url=self.url, token=os.getenv("TOKEN") or "", org=self.org, enable_gzip=True
        )
        # Setup async write API with success/error callbacks
        def success_callback(conf, data):
            # Called when write succeeds
            self._on_write_success(conf, data)
            
        def error_callback(conf, data, error):
            # Called when write fails  
            self._on_write_error(conf, data, error)
            
        def retry_callback(conf, data, error):
            # Called when write is retried
            self._on_write_retry(conf, data, error)

        self.write_api = self.client.write_api(
            write_options=ASYNCHRONOUS,
            success_callback=success_callback,
            error_callback=error_callback,
            retry_callback=retry_callback
        )

    def count_total_messages(self, file: IO[bytes], is_csv: bool = False, estimate: bool = False) -> int:
        """Count total messages in file. If estimate=True, samples first portion for speed."""
        if not self.enable_progress_counting:
            return 0  # Skip counting entirely for very large files
            
        total = 0
        # Ensure we're at the start for reading
        try:
            file.seek(0)
        except Exception:
            pass

        if not is_csv:
            with zipfile.ZipFile(file, "r") as z:
                for file_info in z.infolist():
                    if not file_info.filename.endswith(".csv"):
                        continue

                    # Filename must be a timestamp we can parse, same as process_file
                    filename = os.path.basename(file_info.filename)
                    try:
                        datetime.strptime(filename[:-4], "%Y-%m-%d-%H-%M-%S")
                    except ValueError:
                        # Skip files that process_file would skip
                        continue

                    with z.open(file_info) as f:
                        text_iter = (
                            line.replace("\x00", "")
                            for line in io.TextIOWrapper(
                                f, encoding="utf-8", errors="replace", newline=""
                            )
                        )
                        reader = csv.reader(text_iter)
                        sample_count = 0
                        valid_rows = 0
                        for row in reader:
                            if estimate and sample_count > 1000:  # Sample first 1000 rows
                                # Estimate based on file size ratio
                                file_pos = f.tell() if hasattr(f, 'tell') else 0
                                if file_pos > 0:
                                    estimated_total = int((valid_rows * file_info.file_size) / file_pos)
                                    return estimated_total
                                break
                            sample_count += 1
                            # Basic row shape + timestamp column present
                            if len(row) < 11 or not row[0]:
                                continue
                            # Must have exactly 8 byte columns
                            try:
                                byte_values = [int(b) for b in row[3:11] if b]
                            except Exception:
                                continue
                            if len(byte_values) != 8:
                                continue
                            # Message id must exist in the DBC
                            try:
                                msg_id = int(row[2])
                                # Will raise if not found
                                self.db.get_message_by_frame_id(msg_id)  # type:ignore
                            except Exception:
                                continue
                            total += 1
                            valid_rows += 1
        else:
            # Treat file as a binary stream containing a single CSV
            wrapper_created = False
            text_stream = file
            if not isinstance(file, io.TextIOBase):
                text_stream = io.TextIOWrapper(
                    file, encoding="utf-8", errors="replace", newline=""
                )
                wrapper_created = True

            # Best-effort filename parsing if available
            filename = os.path.basename(getattr(file, "name", ""))
            if filename.endswith(".csv"):
                try:
                    datetime.strptime(filename[:-4], "%Y-%m-%d-%H-%M-%S")
                except ValueError:
                    pass

            try:
                for line in text_stream:
                    line = str(line).replace("\x00", "")
                    row = next(csv.reader([line]))
                    if len(row) < 11 or not row[0]:
                        continue
                    try:
                        byte_values = [int(b) for b in row[3:11] if b]
                    except Exception:
                        continue
                    if len(byte_values) != 8:
                        continue

                    try:
                        msg_id = int(row[2])
                        self.db.get_message_by_frame_id(msg_id)  # type:ignore
                    except Exception:
                        continue
                    total += 1
            finally:
                if wrapper_created:
                    # Prevent closing the underlying BytesIO when the wrapper is garbage collected
                    try:
                        text_stream.detach()  # type:ignore
                    except:
                        pass

        # Rewind for subsequent reads
        try:
            file.seek(0)
        except Exception:
            pass
        return total

    def _parse_row_generator(
        self, row: List[str], start_dt: datetime, filename: str
    ) -> Generator[Point, None, None]:
        # Convert to generator for lazy yielding
        try:
            if len(row) < 11 or not row[0]:
                return

            relative_ms = int(row[0])
            msg_id = int(row[2])
            byte_values = [int(b) for b in row[3:11] if b]
            if len(byte_values) != 8:
                return

            timestamp = (start_dt + timedelta(milliseconds=relative_ms)).astimezone(
                timezone.utc
            )
            message = self.db.get_message_by_frame_id(msg_id)  # type:ignore
            decoded = message.decode(bytes(byte_values))

            for sig_name, raw in decoded.items():  # type:ignore
                sig = message.get_signal_by_name(sig_name)
                unit = getattr(sig, "unit", "N/A")
                desc = getattr(sig, "comment", "") or "No description"
                val = (
                    float(raw.value)  # type:ignore
                    if hasattr(raw, "value")
                    else float(raw)  # type:ignore
                )
                label = raw.name if hasattr(raw, "name") else str(raw)  # type:ignore

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
                yield pt

        except Exception:
            return

    def _stream_csv_rows(self, text_iter, start_dt: datetime, filename: str):
        """Generator that yields points one by one to avoid memory buildup"""
        reader = csv.reader(text_iter)
        for row in reader:
            yield from self._parse_row_generator(row, start_dt, filename)

    async def process_file(
        self,
        file_info,
        z: Optional[zipfile.ZipFile],
        queue,
        semaphore,
        provided_filename: Optional[str] = None,
    ):
        if z:
            async with semaphore:
                filename = os.path.basename(file_info.filename)
                try:
                    start_dt = datetime.strptime(
                        filename[:-4], "%Y-%m-%d-%H-%M-%S"
                    ).replace(tzinfo=self.tz_toronto)
                except ValueError:
                    print(f"Skipping bad filename format: {filename}")
                    return

                with z.open(file_info) as f:
                    text_iter = (
                        line.replace("\x00", "")
                        for line in io.TextIOWrapper(
                            f, encoding="utf-8", errors="replace", newline=""
                        )
                    )
                    
                    # Stream points in smaller batches to prevent memory buildup
                    batch = []
                    rows_in_batch = 0
                    for point in self._stream_csv_rows(text_iter, start_dt, filename):
                        batch.append(point)
                        rows_in_batch += 1
                        if len(batch) >= self.batch_size:
                            # Wait for queue space if needed to prevent unlimited memory growth
                            while queue.qsize() >= self.max_queue_size:
                                await asyncio.sleep(0.01)
                            await queue.put((batch.copy(), rows_in_batch))
                            batch.clear()
                            rows_in_batch = 0
                    if batch:
                        await queue.put((batch, rows_in_batch))
        else:
            async with semaphore:
                filename = os.path.basename(
                    provided_filename or getattr(file_info, "name", "")
                )
                try:
                    start_dt = datetime.strptime(
                        filename[:-4], "%Y-%m-%d-%H-%M-%S"
                    ).replace(tzinfo=self.tz_toronto)
                except ValueError:
                    print(f"Skipping bad filename format: {filename}")
                    return
                text_stream = io.TextIOWrapper(
                    file_info, encoding="utf-8", errors="replace", newline=""
                )
                batch = []
                rows_in_batch = 0
                try:
                    text_iter = (line.replace("\x00", "") for line in text_stream)
                    for point in self._stream_csv_rows(text_iter, start_dt, filename):
                        batch.append(point)
                        rows_in_batch += 1
                        if len(batch) >= self.batch_size:
                            # Wait for queue space if needed to prevent unlimited memory growth
                            while queue.qsize() >= self.max_queue_size:
                                await asyncio.sleep(0.01)
                            await queue.put((batch.copy(), rows_in_batch))
                            batch.clear()
                            rows_in_batch = 0
                finally:
                    try:
                        text_stream.detach()
                    except Exception:
                        pass

                if batch:
                    await queue.put((batch, rows_in_batch))

    async def _producer(
        self,
        file: IO[bytes],
        queue: asyncio.Queue,
        is_csv: bool = False,
        csv_filename: Optional[str] = None,
    ):
        semaphore = asyncio.Semaphore(2)  # Limit to 2 files concurrently
        if not is_csv:
            with zipfile.ZipFile(file, "r") as z:
                for file_info in z.infolist():  # Process sequentially to save memory
                    if file_info.filename.endswith(".csv"):
                        await self.process_file(file_info, z, queue, semaphore)
        else:
            task = asyncio.create_task(
                self.process_file(
                    file,
                    z=None,
                    queue=queue,
                    semaphore=semaphore,
                    provided_filename=csv_filename,
                )
            )
            await asyncio.gather(task)

    async def stream_to_influx(
        self,
        file: IO[bytes],
        is_csv: bool = False,
        on_progress: Optional[Callable[[int, int], None]] = None,
        csv_filename: Optional[str] = None,
        estimate_count: bool = False,
    ):
        if not is_csv:
            total_rows = self.count_total_messages(file, estimate=estimate_count) if self.enable_progress_counting else 0
            if on_progress and total_rows > 0:
                on_progress(0, total_rows)
            try:
                file.seek(0)
            except Exception:
                pass
            progress = ProgressStats(total_rows=total_rows, start_time=time.time())

            queue = asyncio.Queue(maxsize=self.max_queue_size)

            producer = asyncio.create_task(self._producer(file, queue))
            consumers = [
                asyncio.create_task(self._uploader(queue, progress, on_progress))
                for _ in range(self.max_concurrent_uploads * 2)
            ]

            await producer
            # Wait until all queued work items have been processed
            await queue.join()
            # Now signal consumers to exit
            for _ in consumers:
                await queue.put(None)
            await asyncio.gather(*consumers)
        else:
            total_rows = self.count_total_messages(file, is_csv, estimate=estimate_count) if self.enable_progress_counting else 0
            if on_progress and total_rows > 0:
                on_progress(0, total_rows)
            progress = ProgressStats(total_rows=total_rows, start_time=time.time())

            queue = asyncio.Queue(maxsize=self.max_queue_size)

            producer = asyncio.create_task(
                self._producer(file, queue, is_csv, csv_filename)
            )
            consumers = [
                asyncio.create_task(self._uploader(queue, progress, on_progress))
                for _ in range(self.max_concurrent_uploads * 2)
            ]

            await producer
            # Wait until all queued work items have been processed
            await queue.join()
            # Now signal consumers to exit
            for _ in consumers:
                await queue.put(None)
            await asyncio.gather(*consumers)
            
        # Wait for all pending async writes to complete
        await self._wait_for_pending_writes(progress)

        elapsed = time.time() - progress.start_time
        rate = (progress.processed_rows/elapsed) if elapsed else 0
        if progress.total_rows > 0:
            print(
                f"\n✅ Finished streaming {progress.processed_rows:,}/{progress.total_rows:,} rows in {elapsed:.2f}s ({rate:.1f} rows/s)"
            )
        else:
            print(
                f"\n✅ Finished streaming {progress.processed_rows:,} rows in {elapsed:.2f}s ({rate:.1f} rows/s)"
            )

    async def stream_large_file(
        self,
        file: IO[bytes],
        is_csv: bool = False,
        csv_filename: Optional[str] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ):
        """
        Optimized method for very large files that skips row counting and uses minimal memory.
        Progress will be reported as processed count only (total unknown).
        """
        # Temporarily disable progress counting for maximum performance
        original_counting = self.enable_progress_counting
        self.enable_progress_counting = False
        
        try:
            await self.stream_to_influx(
                file=file,
                is_csv=is_csv,
                csv_filename=csv_filename,
                on_progress=lambda processed, _: on_progress(processed, 0) if on_progress else None,
                estimate_count=False
            )
        finally:
            self.enable_progress_counting = original_counting

    async def _uploader(
        self,
        queue: asyncio.Queue,
        stats: ProgressStats,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ):
        while True:
            item = await queue.get()
            if item is None:
                # acknowledge the sentinel so join() isn't blocked
                queue.task_done()
                break
            batch_points, rows_in_batch = item
            
            # Rate limiting with adaptive backoff
            delay = self._calculate_adaptive_delay()
            if delay > 0:
                await asyncio.sleep(delay)
            
            try:
                # Create unique callback ID for tracking
                callback_id = f"{id(batch_points)}_{time.time()}"
                
                # Store callback info for tracking
                async with self._callback_lock:
                    self._write_callbacks[callback_id] = {
                        'rows_in_batch': rows_in_batch,
                        'stats': stats,
                        'on_progress': on_progress,
                        'failed': False,
                        'timestamp': time.time()
                    }
                
                # Submit async write (non-blocking)
                self.write_api.write(
                    bucket=self.bucket, 
                    org=self.org, 
                    record=batch_points,
                    _callback_id=callback_id  # Custom attribute for tracking
                )
                
                # Update stats optimistically (will be corrected in error callback if needed)
                stats.processed_rows += rows_in_batch
                stats.pending_writes += 1
                if on_progress:
                    on_progress(stats.processed_rows, stats.total_rows)
                    
            except Exception as e:
                # Immediate synchronous error (before async operation)
                stats.failed_rows += rows_in_batch
                self._consecutive_failures += 1
                print(f"❌ Failed to submit async write: {e}")
                
            # mark this work item as done
            queue.task_done()

    def configure_for_file_size(self, estimated_size_mb: float):
        """
        Automatically configure settings based on estimated file size for optimal performance.
        Includes InfluxDB-safe rate limiting to prevent overwhelming the database.
        
        Args:
            estimated_size_mb: Estimated file size in megabytes
        """
        if estimated_size_mb < 10:  # Small files
            self.batch_size = 500
            self.max_concurrent_uploads = 3  # Reduced from 5
            self.max_queue_size = 50
            self.rate_limit_delay = 0.005  # 5ms delay
            self.enable_progress_counting = True
        elif estimated_size_mb < 100:  # Medium files  
            self.batch_size = 1000
            self.max_concurrent_uploads = 5  # Reduced from 8
            self.max_queue_size = 100
            self.rate_limit_delay = 0.01  # 10ms delay
            self.enable_progress_counting = True
        elif estimated_size_mb < 1000:  # Large files
            self.batch_size = 2000
            self.max_concurrent_uploads = 6  # Reduced from 10
            self.max_queue_size = 50
            self.rate_limit_delay = 0.02  # 20ms delay
            self.enable_progress_counting = True  # Use estimation
        else:  # Very large files (1GB+)
            self.batch_size = 3000  # Reduced from 5000
            self.max_concurrent_uploads = 8  # Reduced from 15
            self.max_queue_size = 20
            self.rate_limit_delay = 0.05  # 50ms delay for safety
            self.enable_progress_counting = False  # Skip counting entirely
            
            print(f"📊 Configured for {estimated_size_mb:.1f}MB file: batch_size={self.batch_size}, "
              f"concurrent_uploads={self.max_concurrent_uploads}, queue_size={self.max_queue_size}, "
              f"rate_limit_delay={self.rate_limit_delay}s, "
              f"progress_counting={'enabled' if self.enable_progress_counting else 'disabled'}")

    def _calculate_adaptive_delay(self) -> float:
        """Calculate adaptive delay based on recent failures to prevent overwhelming InfluxDB."""
        if not self.adaptive_backoff or self._consecutive_failures == 0:
            return self.rate_limit_delay
            
        # Exponential backoff: base_delay * 2^failures (capped at 5 seconds)
        adaptive_delay = self.rate_limit_delay * (2 ** min(self._consecutive_failures, 10))
        return min(adaptive_delay, 5.0)

    def _on_write_success(self, conf, data):
        """Callback for successful async writes"""
        # Reset failure counter on success
        self._consecutive_failures = 0
        
        # Remove from pending callbacks and update progress
        callback_id = getattr(conf, '_callback_id', None)
        if callback_id and callback_id in self._write_callbacks:
            callback_info = self._write_callbacks.pop(callback_id)
            # Progress was already updated optimistically, no need to update again
    
    def _on_write_error(self, conf, data, error):
        """Callback for failed async writes"""
        self._consecutive_failures += 1
        self._last_error_time = time.time()
        print(f"❌ Async write failed: {error}")
        
        # Handle failed callback - correct optimistic progress
        callback_id = getattr(conf, '_callback_id', None)
        if callback_id and callback_id in self._write_callbacks:
            callback_info = self._write_callbacks.pop(callback_id)
            rows_in_batch = callback_info['rows_in_batch']
            stats = callback_info['stats']
            
            # Correct the optimistic progress update
            stats.processed_rows -= rows_in_batch
            stats.failed_rows += rows_in_batch
    
    def _on_write_retry(self, conf, data, error):
        """Callback for retried async writes"""
        print(f"🔄 Retrying async write: {error}")

    async def _wait_for_pending_writes(self, stats: ProgressStats, timeout: float = 30.0):
        """Wait for all pending async writes to complete"""
        start_time = time.time()
        print(f"⏳ Waiting for {len(self._write_callbacks)} pending writes to complete...")
        
        while len(self._write_callbacks) > 0 and (time.time() - start_time) < timeout:
            await asyncio.sleep(0.1)
            
            # Clean up completed callbacks
            async with self._callback_lock:
                completed_callbacks = []
                for callback_id, info in self._write_callbacks.items():
                    # Check if callback is old (likely completed but not cleaned up)
                    if time.time() - info['timestamp'] > 10.0:
                        completed_callbacks.append(callback_id)
                
                for callback_id in completed_callbacks:
                    self._write_callbacks.pop(callback_id, None)
                    stats.pending_writes = max(0, stats.pending_writes - 1)
        
        if len(self._write_callbacks) > 0:
            print(f"⚠️ {len(self._write_callbacks)} writes still pending after timeout")
        else:
            print("✅ All async writes completed")

    def close(self):
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
