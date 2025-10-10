# file_processor.py
import zipfile
import csv
import io
# import pandas as pd # No longer needed for this script's core processing
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo  # Standard in Python 3.9+
import os
import shutil
import time
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import cantools  # For DBC parsing
from influxdb_client import Point, InfluxDBClient
from influxdb_client.client.write_api import WriteOptions
from tqdm import tqdm  # For progress bars
import requests  # For sending webhook notifications


# --- Configuration ---
# (Same as before)
INGEST_DIR = os.getenv("INGEST_DIRECTORY", "/ubuntu/data-ingest")
PROCESSED_DIR = os.getenv("PROCESSED_DIRECTORY", os.path.join(INGEST_DIR, "processed"))
DBC_FILE_PATH = os.getenv("DBC_FILE_PATH", "WFR25-f772b40.dbc")
INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://influxwfr:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN",
                           "s9XkBC7pKOlb92-N9M40qilmxxoBe4wrnki4zpS_o0QSVTuMSQRQBerQB9Zv0YV40tmYayuX3w4G2MNizdy3qw==")

WEBHOOK_URL = "https://hooks.slack.com/services/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"

INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "WFR")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "WFR2025")
TEMP_CLEANED_CSV_NAME = "temp_cleaned_data.csv"
ERROR_LOG_FILE = os.getenv("ERROR_LOG_FILE", "parse_errors.log")


os.makedirs(PROCESSED_DIR, exist_ok=True)

# --- Logging Setup ---
# (Same as before)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
error_logger = logging.getLogger('CANParseErrorLogger')
error_file_handler = logging.FileHandler(ERROR_LOG_FILE, mode='a')
error_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
error_logger.addHandler(error_file_handler)
error_logger.setLevel(logging.ERROR)


def send_webhook_notification(payload_text=None):
    try:
        payload = {"text": payload_text}
        response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
        error_logger.info("Webhook notification sent successfully.")
    except requests.exceptions.RequestException as e:
        error_logger.error(f"Webhook notification failed: {e}")


# === Part 1: CSV Cleaning Logic (Memory Optimized) ===
def clean_zip_csv(zip_path, output_csv_path):
    logging.info(f"Starting CSV cleaning for {zip_path} (streaming to {output_csv_path})")

    try:
        tz_toronto = ZoneInfo("America/Toronto")
        tz_utc = ZoneInfo("UTC")
    except Exception as e:
        logging.error(f"Failed to load timezones. Ensure tzdata is available. Error: {e}")
        return False

    header = [
        "timestamp", "relative_time_ms", "message", "message_id",
        "byte0", "byte1", "byte2", "byte3", "byte4", "byte5", "byte6", "byte7"
    ]
    rows_written_count = 0
    csv_files_found_in_zip = 0

    try:
        with open(output_csv_path, 'w', newline='', encoding='utf-8') as outfile:
            csv_writer = csv.writer(outfile)
            csv_writer.writerow(header)

            with zipfile.ZipFile(zip_path, 'r') as z:
                for file_info in z.infolist():
                    if file_info.filename.endswith('.csv') and not file_info.is_dir():
                        csv_files_found_in_zip += 1
                        logging.info(f"Processing CSV file in zip: {file_info.filename}")
                        filename_in_zip = file_info.filename.split('/')[-1]
                        try:
                            start_dt = datetime.strptime(filename_in_zip[:-4], "%Y-%m-%d-%H-%M-%S")
                        except ValueError:
                            logging.warning(
                                f"Skipping file with unexpected name format: {filename_in_zip} in {zip_path}")
                            continue
                        start_dt = start_dt.replace(tzinfo=tz_toronto)

                        with z.open(file_info) as f_in_zip:
                            text_stream = io.TextIOWrapper(f_in_zip, encoding='utf-8', newline='')
                            reader = csv.reader(text_stream)
                            for i, row_content in enumerate(reader):
                                if len(row_content) < 11:
                                    continue
                                try:
                                    relative_ms_str = row_content[0]
                                    if not relative_ms_str: continue
                                    relative_ms = int(relative_ms_str)
                                    msg = row_content[1]
                                    msg_id = row_content[2]
                                    byte_strings = row_content[3:11]
                                    if len(byte_strings) < 8: continue

                                    byte_values = []
                                    valid_bytes = True
                                    for b_str in byte_strings:
                                        if not b_str: valid_bytes = False; break
                                        b_int = int(b_str)
                                        if not (0 <= b_int <= 255): valid_bytes = False; break
                                        byte_values.append(b_int)
                                    if not valid_bytes: continue
                                except (ValueError, IndexError) as e:
                                    logging.debug(
                                        f"Row {i + 1} in {filename_in_zip}: Skipping due to data error: {row_content}. Error: {e}")
                                    continue

                                ts_local = start_dt + timedelta(milliseconds=relative_ms)
                                ts_utc_dt = ts_local.astimezone(tz_utc)
                                epoch_seconds = ts_utc_dt.timestamp()

                                output_row_values = [
                                                        epoch_seconds, relative_ms, msg, msg_id,
                                                    ] + byte_values  # byte_values is list of 8 integers

                                csv_writer.writerow(output_row_values)
                                rows_written_count += 1

                if csv_files_found_in_zip == 0:
                    logging.warning(f"No CSV files found in {zip_path}. Output CSV will be header-only.")

        if rows_written_count > 0:
            logging.info(f"Saved cleaned data to {output_csv_path}. Total rows written: {rows_written_count}")
        else:
            logging.info(
                f"No data rows were processed/written from {zip_path}. Cleaned CSV {output_csv_path} contains only header (if any CSVs were found).")
        return True  # Indicates completion, downstream checks content

    except FileNotFoundError:  # For zip_path
        logging.error(f"Zip file not found: {zip_path}")
        return False
    except zipfile.BadZipFile:
        logging.error(f"Bad zip file (corrupt or not a zip): {zip_path}")
        return False
    except IOError as e:  # For issues writing to output_csv_path
        logging.error(f"IOError writing to {output_csv_path}: {e}", exc_info=True)
        return False
    except Exception as e:
        logging.error(f"Critical error during CSV cleaning for {zip_path}: {e}", exc_info=True)
        return False


# === Part 2: InfluxDB Upload Logic ===
# (This function, count_lines_in_csv, parse_can_csv_row_for_influx, and upload_to_influxdb
# remain the same as in the previous full script. They already stream data from the
# cleaned CSV and should be memory efficient enough for your use case.)
def count_lines_in_csv(file_path):
    try:
        with open(file_path, 'r', newline='', encoding='utf-8') as f:
            return sum(1 for _ in f) - 1
    except FileNotFoundError:
        return 0
    except Exception as e:
        logging.error(f"Error counting lines in {file_path}: {e}")
        return 0


def parse_can_csv_row_for_influx(row_dict, db, signal_cache):
    # ... (same implementation as previous answer)
    try:
        can_id_int = int(row_dict['message_id'])
    except (KeyError, ValueError):
        error_logger.error(f"Invalid or missing 'message_id': '{row_dict.get('message_id')}' in row: {row_dict}")
        return None

    try:
        message_spec = db.get_message_by_frame_id(can_id_int)
    except KeyError:
        error_logger.error(f"No message definition found in DBC for CAN ID {can_id_int}")
        return None

    try:
        data_bytes_list = [int(row_dict[f'byte{b}']) for b in range(8)]
        data_bytes = bytes(data_bytes_list)
    except KeyError as e:
        error_logger.error(f"Missing byteX field in cleaned CSV for CAN ID {can_id_int}. Row: {row_dict}. Error: {e}")
        return None
    except ValueError as e:
        error_logger.error(
            f"Non-integer byte value in cleaned CSV for CAN ID {can_id_int}. Row: {row_dict}. Error: {e}")
        return None

    if len(data_bytes) < message_spec.length:
        logging.debug(
            f"Data for CAN ID {can_id_int} has {len(data_bytes)} bytes, but DBC message {message_spec.name} expects {message_spec.length}. Decoding will proceed with allow_truncated=True.")

    try:
        decoded_signals = message_spec.decode(data_bytes, allow_truncated=True, decode_choices=False)
    except Exception as e:
        error_logger.error(
            f"Decoding error for CAN ID {message_spec.name} ({can_id_int}) with data {list(data_bytes)}: {e}")
        return None

    try:
        ts = float(row_dict['timestamp'])
        ts_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (KeyError, ValueError, TypeError) as e:
        logging.warning(
            f"Could not parse timestamp '{row_dict.get('timestamp')}' for CAN ID {can_id_int}. Error: {e}. Using current time.")
        ts_dt = datetime.now(timezone.utc)

    points = []
    for sig_name, raw_value in decoded_signals.items():
        try:
            sig_spec = message_spec.get_signal_by_name(sig_name)
            if (message_spec.name, sig_name) in signal_cache:
                desc, unit = signal_cache[(message_spec.name, sig_name)]
            else:
                desc = getattr(sig_spec, 'comment', None) or "No description"
                unit = getattr(sig_spec, 'unit', None) or "N/A"
                signal_cache[(message_spec.name, sig_name)] = (desc, unit)

            val_to_write = float(raw_value)
            label = str(raw_value)

            if sig_spec.choices and raw_value in sig_spec.choices:
                label = sig_spec.choices[raw_value]

        except Exception as e:
            error_logger.error(f"Error processing signal '{sig_name}' (CAN ID {can_id_int}), value '{raw_value}': {e}")
            continue

        pt = (
            Point("canBus")
            .tag("signalName", sig_name)
            .tag("messageName", message_spec.name)
            .tag("canID", str(can_id_int))
            .field("sensorReading", val_to_write)
            .field("unit", unit)
            .field("signalLabel", label)
            .time(ts_dt)
        )
        points.append(pt)
    return points


def upload_to_influxdb(cleaned_csv_path, dbc_file, influx_url, influx_token, influx_org, influx_bucket):
    # ... (same implementation as previous answer)
    logging.info(f"Starting InfluxDB upload for {cleaned_csv_path} using DBC {dbc_file}")
    try:
        db = cantools.database.load_file(dbc_file)
        logging.info(f"Successfully loaded DBC: {dbc_file}")
    except Exception as e:
        logging.error(f"Failed loading DBC {dbc_file}: {e}")
        return False

    write_options = WriteOptions(batch_size=10_000, flush_interval=1_000, jitter_interval=200, retry_interval=5_000)
    try:
        client = InfluxDBClient(url=influx_url, token=influx_token, org=influx_org)
        if not client.ping():
            logging.error(f"Cannot connect to InfluxDB at {influx_url}. Please check URL and credentials.")
            return False
        write_api = client.write_api(write_options=write_options)
    except Exception as e:
        logging.error(f"Failed to initialize InfluxDB client or ping server: {e}")
        return False

    points_written_count = 0
    rows_processed_count = 0
    signal_cache = {}

    total_data_lines = count_lines_in_csv(cleaned_csv_path)
    if total_data_lines <= 0:
        if os.path.exists(cleaned_csv_path):
            logging.info(f"Cleaned CSV {cleaned_csv_path} is empty or contains only a header. No data to upload.")
            return True
        else:
            logging.error(f"Cleaned CSV {cleaned_csv_path} not found. Cannot upload to InfluxDB.")
            return False

    try:
        with open(cleaned_csv_path, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row_dict in tqdm(reader, total=total_data_lines, desc="Uploading to InfluxDB"):
                rows_processed_count += 1
                parsed_points = parse_can_csv_row_for_influx(row_dict, db, signal_cache)
                if parsed_points:
                    write_api.write(bucket=influx_bucket, org=influx_org, record=parsed_points)
                    points_written_count += len(parsed_points)

        logging.info(
            f"Finished InfluxDB upload. Processed {rows_processed_count} rows from CSV. Wrote approximately {points_written_count} points.")
        logging.info(f"CAN parsing errors (if any) logged in {ERROR_LOG_FILE}")
        return True

    except FileNotFoundError:
        logging.error(f"Cleaned CSV file not found during upload: {cleaned_csv_path}")
        return False
    except Exception as e:
        logging.error(f"Error during InfluxDB upload for {cleaned_csv_path}: {e}", exc_info=True)
        return False
    finally:
        if 'write_api' in locals(): write_api.close()
        if 'client' in locals(): client.close()


# === File System Event Handler ===
# (ZipFileHandler implementation remains the same as in the previous full script)
class ZipFileHandler(FileSystemEventHandler):
    def __init__(self, ingest_dir, processed_dir, temp_csv_name, dbc_path, influx_url, influx_token, influx_org,
                 influx_bucket):
        # ... (same implementation as previous answer)
        self.ingest_dir = ingest_dir
        self.processed_dir = processed_dir
        self.temp_csv_name = temp_csv_name
        self.dbc_path = dbc_path
        self.influx_url = influx_url
        self.influx_token = influx_token
        self.influx_org = influx_org
        self.influx_bucket = influx_bucket
        self.processing_files = set()

    def on_created(self, event):
        # ... (same implementation as previous answer)
        if event.is_directory:
            return

        src_path = event.src_path

        if src_path in self.processing_files:
            logging.debug(f"File {src_path} is already being processed or was just processed. Skipping.")
            return

        if not src_path.endswith('.zip') or os.path.basename(src_path).startswith('.'):
            if src_path.endswith('.zip'):
                logging.info(f"Ignoring hidden or temporary zip file: {src_path}")
            return

        self.processing_files.add(src_path)
        logging.info(f"New zip file detected: {src_path}")
        time.sleep(5)

        unique_temp_suffix = datetime.now().strftime("%Y%m%d%H%M%S%f")
        temp_cleaned_csv_full_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"{os.path.splitext(self.temp_csv_name)[0]}_{unique_temp_suffix}.csv"
        )

        move_original_to_processed = False
        process_successful = False

        try:
            cleaning_ok = clean_zip_csv(src_path, temp_cleaned_csv_full_path)

            if cleaning_ok:
                # Check if the cleaned CSV actually has data (more than just a header)
                if os.path.exists(temp_cleaned_csv_full_path) and count_lines_in_csv(temp_cleaned_csv_full_path) > 0:
                    logging.info(
                        f"Cleaning successful. {count_lines_in_csv(temp_cleaned_csv_full_path)} data lines in {temp_cleaned_csv_full_path}.")
                    upload_ok = upload_to_influxdb(
                        temp_cleaned_csv_full_path, self.dbc_path,
                        self.influx_url, self.influx_token, self.influx_org, self.influx_bucket
                    )
                    if upload_ok:
                        logging.info(f"Successfully processed and uploaded data from {src_path}.")
                        send_webhook_notification(f"Successfully processed and uploaded data from {src_path}.")
                        move_original_to_processed = True
                        process_successful = True
                    else:
                        logging.error(f"InfluxDB upload failed for data from {src_path}.")
                # Case: Cleaning was OK, but the resulting temp CSV is empty (header-only or 0 actual data lines)
                elif os.path.exists(temp_cleaned_csv_full_path):
                    logging.info(
                        f"Cleaning of {src_path} resulted in an empty or header-only data file. No InfluxDB upload needed.")
                    move_original_to_processed = True
                    process_successful = True
                    # Case: Cleaning reported success (returned True), but no temp file was actually created.
                # This might happen if clean_zip_csv found no CSVs in the zip and wrote nothing.
                else:
                    logging.warning(
                        f"Cleaning of {src_path} reported completion but no output file found or it's invalid at {temp_cleaned_csv_full_path}. Assuming no data to process.")
                    move_original_to_processed = True
                    process_successful = True
            else:  # cleaning_ok is False
                logging.error(f"CSV cleaning failed for {src_path}.")

        except Exception as e:
            logging.error(f"Unhandled critical exception during processing of {src_path}: {e}", exc_info=True)

        finally:
            if move_original_to_processed:
                try:
                    base_filename = os.path.basename(src_path)
                    dest_path = os.path.join(self.processed_dir, base_filename)
                    counter = 1
                    name, ext = os.path.splitext(base_filename)
                    while os.path.exists(dest_path):
                        dest_path = os.path.join(self.processed_dir, f"{name}_{counter}{ext}")
                        counter += 1
                    shutil.move(src_path, dest_path)
                    logging.info(f"Moved {src_path} to {dest_path}")
                except Exception as e:
                    logging.error(f"Failed to move {src_path} to processed directory {self.processed_dir}: {e}",
                                  exc_info=True)
            else:
                logging.warning(
                    f"File {src_path} was not successfully processed or yielded no data to upload that warrants moving. It remains in {self.ingest_dir}.")

            if os.path.exists(temp_cleaned_csv_full_path):
                try:
                    os.remove(temp_cleaned_csv_full_path)
                    logging.info(f"Removed temporary file: {temp_cleaned_csv_full_path}")
                    send_webhook_notification(f"Removed temporary file: {temp_cleaned_csv_full_path}")
                except OSError as e:
                    logging.error(f"Error removing temporary file {temp_cleaned_csv_full_path}: {e}")

            if src_path in self.processing_files:  # Ensure it's removed only if added
                self.processing_files.remove(src_path)


# === Main Execution ===
# (Main execution block remains the same as in the previous full script)
if __name__ == "__main__":
    logging.info(f"🚀 Starting File Processor")
    logging.info(f"Monitoring directory: {INGEST_DIR}")
    # ... (rest of main is identical to the previous answer) ...
    logging.info(f"Processed files will be moved to: {PROCESSED_DIR}")
    logging.info(f"Using DBC file: {DBC_FILE_PATH}")
    logging.info(f"InfluxDB Target: URL={INFLUXDB_URL}, Org={INFLUXDB_ORG}, Bucket={INFLUXDB_BUCKET}")
    logging.info(f"Error log for CAN parsing: {ERROR_LOG_FILE}")

    if not os.path.exists(DBC_FILE_PATH):
        logging.critical(f"DBC file not found at {DBC_FILE_PATH}. Please ensure it's available. Exiting.")
        exit(1)
    if not os.path.isdir(INGEST_DIR):
        logging.critical(f"Ingest directory {INGEST_DIR} does not exist or is not a directory. Exiting.")
        exit(1)
    try:
        os.makedirs(PROCESSED_DIR, exist_ok=True)
    except OSError as e:
        logging.critical(f"Could not create/access processed directory {PROCESSED_DIR}: {e}. Exiting.")
        exit(1)

    event_handler = ZipFileHandler(
        ingest_dir=INGEST_DIR,
        processed_dir=PROCESSED_DIR,
        temp_csv_name=TEMP_CLEANED_CSV_NAME,
        dbc_path=DBC_FILE_PATH,
        influx_url=INFLUXDB_URL,
        influx_token=INFLUXDB_TOKEN,
        influx_org=INFLUXDB_ORG,
        influx_bucket=INFLUXDB_BUCKET
    )
    observer = Observer()
    observer.schedule(event_handler, INGEST_DIR, recursive=False)
    observer.start()
    logging.info("👀 Observer started. Waiting for new .zip files...")
    send_webhook_notification("👀 Observer started. Waiting for new .zip files...")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logging.info("🛑 KeyboardInterrupt received. Shutting down observer...")
    finally:
        observer.stop()
        observer.join()
        logging.info("👋 Observer stopped. Exiting.")