# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Install tzdata for timezone support (e.g., America/Toronto, UTC)
# and build-essential for some C dependencies that libraries might need.
RUN apt-get update && \
    apt-get install -y tzdata build-essential && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Note: The COPY commands for file_processor.py and WFR25-f772b40.dbc have been removed.
# These will be mounted at runtime.

# Define environment variables (these can be overridden at runtime)
# IMPORTANT: For production, pass sensitive tokens via `docker run -e` or Docker secrets,
# not by hardcoding them in the image if it's shared.
# ENV INGEST_DIRECTORY="/data/ingest"
# ENV PROCESSED_DIRECTORY="/data/ingest/processed" # Subdirectory of the mounted ingest volume
# ENV DBC_FILE_PATH="/app/WFR25-f772b40.dbc" # Path inside the container where the DBC file will be mounted
# ENV INFLUXDB_URL="http://your_influxdb_host:8086" # <<< CHANGE THIS
# ENV INFLUXDB_TOKEN="your_influxdb_token"         # <<< CHANGE THIS
# ENV INFLUXDB_ORG="your_org"                      # <<< CHANGE THIS
# ENV INFLUXDB_BUCKET="your_bucket"                # <<< CHANGE THIS
ENV LOG_LEVEL="INFO"
ENV ERROR_LOG_FILE="/app/logs/parse_errors.log"  # Path for CAN parse error log

# Create the directory for logs if you plan to write logs to a file inside the container.
# It's often better to mount a volume for logs for persistence.
RUN mkdir -p /app/logs

# Command to run the application when the container launches
CMD ["python", "file_processor.py"]