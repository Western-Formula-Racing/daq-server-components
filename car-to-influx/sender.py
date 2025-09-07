import csv
import json
import time
import requests # For making HTTP requests

# THIS IS FOR TESTING PURPOSES ONLY

# Configuration
FILE_PATH = "testing_data/cleaned_can.csv"
URL = "http://3.98.181.12:8085/can"
BATCH_SIZE = 1000   # frames per POST
RATE = 10        # batches per second (e.g., 1.0 means 1 batch per second)
REQUEST_TIMEOUT = 10 # seconds for request timeout

def parse_csv_line(line_parts):
    """
    Parses a single line from the CSV file into a dictionary
    for the JSON message structure.
    Assumes the CSV format: timestamp,unknown,type,id,d1,d2,d3,d4,d5,d6,d7,d8
    """
    try:
        timestamp_str = line_parts[0]
        # The example JSON has a float timestamp, so we'll convert.
        # If the server expects a string, this can be adjusted.
        timestamp = float(timestamp_str)

        # The ID is the 4th element (index 3) in the CSV line
        message_id = int(line_parts[3])

        # The data fields start from the 5th element (index 4) and there are 8 of them
        data_values = [int(val) for val in line_parts[4:12]]

        return {
            "timestamp": timestamp,
            "id": message_id,
            "data": data_values
        }
    except (ValueError, IndexError) as e:
        print(f"Error parsing line: {line_parts}. Error: {e}")
        return None

def send_batch(batch_messages, session):
    """
    Sends a batch of messages to the server.
    """
    if not batch_messages:
        print("Batch is empty, nothing to send.")
        return

    payload = {"messages": batch_messages}
    headers = {"Content-Type": "application/json"}

    try:
        # Using a session object for potential performance benefits (connection reuse)
        response = session.post(URL, data=json.dumps(payload), headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()  # Raises an HTTPError for bad responses (4XX or 5XX)
        print(f"Successfully sent batch of {len(batch_messages)} messages. Status: {response.status_code}")
        # You can print response.text or response.json() if the server sends back a body
        # print(f"Response: {response.text}")
    except requests.exceptions.HTTPError as http_err:
        print(f"HTTP error occurred: {http_err} - {response.status_code} - {response.text}")
    except requests.exceptions.ConnectionError as conn_err:
        print(f"Connection error occurred: {conn_err}")
    except requests.exceptions.Timeout as timeout_err:
        print(f"Timeout error occurred: {timeout_err}")
    except requests.exceptions.RequestException as req_err:
        print(f"An error occurred during the request: {req_err}")
    except Exception as e:
        print(f"An unexpected error occurred while sending batch: {e}")


def main():
    """
    Main function to read the file, batch messages, and send them.
    """
    messages_batch = []
    batch_count = 0
    lines_processed = 0

    # Use a requests Session for connection pooling
    with requests.Session() as session:
        try:
            with open(FILE_PATH, 'r', newline='') as csvfile:
                reader = csv.reader(csvfile)
                print(f"Reading from file: {FILE_PATH}")

                for i, row_parts in enumerate(reader):
                    lines_processed += 1
                    # Skip empty lines or lines with insufficient columns
                    if not row_parts or len(row_parts) < 12:
                        print(f"Skipping malformed or empty line {i+1}: {row_parts}")
                        continue

                    parsed_message = parse_csv_line(row_parts)
                    if parsed_message:
                        messages_batch.append(parsed_message)

                    if len(messages_batch) >= BATCH_SIZE:
                        batch_count += 1
                        print(f"\n--- Sending Batch {batch_count} ({len(messages_batch)} messages) ---")
                        send_batch(messages_batch, session)
                        messages_batch = []  # Reset batch
                        if RATE > 0:
                            time.sleep(1.0 / RATE) # Control the sending rate

                # Send any remaining messages after the loop
                if messages_batch:
                    batch_count += 1
                    print(f"\n--- Sending Final Batch {batch_count} ({len(messages_batch)} messages) ---")
                    send_batch(messages_batch, session)

            print(f"\nProcessing complete. Processed {lines_processed} lines.")
            print(f"Sent a total of {batch_count} batches.")

        except FileNotFoundError:
            print(f"Error: The file '{FILE_PATH}' was not found.")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    main()
