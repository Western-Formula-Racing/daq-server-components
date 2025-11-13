FROM python:3.12-slim

# Set working dir
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Set default command
CMD ["python", "slackbot.py"]