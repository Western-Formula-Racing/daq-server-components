FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY prompt-guide.txt .

ENV PYTHONUNBUFFERED=1
ENV DATA_DOWNLOADER_URL=http://data-downloader-api:8000
ENV MCP_TRANSPORT=stdio

CMD ["python", "server.py"]
