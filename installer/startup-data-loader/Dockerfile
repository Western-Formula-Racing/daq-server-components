FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Create Telegraf output directory
RUN mkdir -p /var/lib/telegraf

# Make the script executable
RUN chmod +x load_data_influxdb2.py
RUN chmod +x load_data_telegraf.py

# Run both data loaders in parallel
CMD ["sh", "-c", "python load_data_influxdb2.py & python load_data_telegraf.py & wait"]
