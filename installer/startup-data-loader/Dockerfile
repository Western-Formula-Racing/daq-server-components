FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files (exclude the runtime `data/` directory)
# Keep the build context small by only copying the files needed to run the loader.
COPY load_data.py requirements.txt ./
# Copy any .dbc files found at the build context root into the image
COPY *.dbc ./

# Create Telegraf output directory
RUN mkdir -p /var/lib/telegraf

# Make the script executable
RUN chmod +x load_data.py

# Run the Telegraf/direct-write loader
CMD ["python", "load_data.py"]
