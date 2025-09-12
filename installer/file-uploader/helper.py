import zipfile, csv, io, time, asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Optional, IO, Callable, Generator
from zoneinfo import ZoneInfo
from dataclasses import dataclass
import cantools
from influxdb_client.client.influxdb_client import InfluxDBClient
from influxdb_client.client.write.point import Point
from influxdb_client.client.write_api import WriteOptions
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


class CANInfluxStreamer:

    def __init__(
        self, bucket: str, batch_size: int = 500, max_concurrent_uploads: int = 5
    ):

        self.batch_size = batch_size
        self.max_concurrent_uploads = max_concurrent_uploads
        self.bucket = bucket
        self.org = "WFR"
        self.tz_toronto = ZoneInfo("America/Toronto")
        self.url = (
            "http://3.98.181.12:8086"
            if bool(int(os.getenv("DEBUG") or 1))
            else "http://influxwfr:8086"
        )
        # self.url = "http://influxwfr:8086"
        # self.url = "http://3.98.181.12:8086" # Comment out for prod

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
        self.write_api = self.client.write_api(
            write_options=WriteOptions(batch_size=batch_size, flush_interval=10_000)
        )

    def count_total_messages(self, file: IO[bytes], is_csv: bool = False) -> int:
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
                        for row in reader:
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
                    reader = csv.reader(text_iter)
                    batch = []
                    rows_in_batch = 0
                    for row in reader:
                        for point in self._parse_row_generator(row, start_dt, filename):
                            batch.append(point)
                            rows_in_batch += 1
                            if len(batch) >= self.batch_size:
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
                    for line in text_stream:
                        line = line.replace("\x00", "")
                        row = next(csv.reader([line]))
                        for point in self._parse_row_generator(row, start_dt, filename):
                            batch.append(point)
                            rows_in_batch += 1
                            if len(batch) >= self.batch_size:
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
    ):
        if not is_csv:
            total_rows = self.count_total_messages(file)
            if on_progress:
                on_progress(0, total_rows)
            file.seek(0)
            progress = ProgressStats(total_rows=total_rows, start_time=time.time())

            queue = asyncio.Queue(maxsize=self.max_concurrent_uploads * 2)

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
            total_rows = self.count_total_messages(file, is_csv)
            if on_progress:
                on_progress(0, total_rows)
            progress = ProgressStats(total_rows=total_rows, start_time=time.time())

            queue = asyncio.Queue(maxsize=self.max_concurrent_uploads * 2)

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

        elapsed = time.time() - progress.start_time
        print(
            f"\n✅ Finished streaming {progress.processed_rows:,} rows in {elapsed:.2f}s ({(progress.processed_rows/elapsed) if elapsed else 0:.1f} rows/s)"
        )

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
            try:
                self.write_api.write(
                    bucket=self.bucket, org=self.org, record=batch_points
                )
                stats.processed_rows += rows_in_batch
                if on_progress:
                    on_progress(stats.processed_rows, stats.total_rows)
            except Exception as e:
                stats.failed_rows += rows_in_batch
                print(f"❌ Failed to write batch: {e}")
            finally:
                # mark this work item as done whether it succeeded or failed
                queue.task_done()

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
