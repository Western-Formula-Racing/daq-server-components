# Dockerfile for car-to-influx

# 1. Base image
FROM python:3.12-slim

# 2. Set working directory
WORKDIR /app

# 3. Install build tools (for cantools) and clean up apt cache
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc \
 && rm -rf /var/lib/apt/lists/*

# 4. Install Python dependencies
#    If you have a requirements.txt, copy & install it; otherwise install explicitly:
# COPY requirements.txt .
# RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir \
      flask \
      influxdb-client \
      requests \
      cantools

# 5. Copy application code
COPY . .

# 6. Expose the port your Flask app listens on
EXPOSE 8085

# 7. Default command
CMD ["python", "listener.py"]