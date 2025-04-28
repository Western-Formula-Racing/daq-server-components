# 1. Base image: use slim Python to keep it lightweight
FROM python:3.12-slim

# 2. Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 3. Set working directory inside container
WORKDIR /app

# 4. Install system dependencies (optional but helps)
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 5. Install Python dependencies
RUN pip install --no-cache-dir dash plotly flask-cors

# 6. Copy the application code into container
COPY . .

# 7. Expose the port the app will run on
EXPOSE 8050

# 8. Command to run the app
CMD ["python", "lap.py"]
