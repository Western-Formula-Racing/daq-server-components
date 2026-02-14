FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY code_generator.py .
COPY prompt-guide.txt .

# Create directory for generated code
RUN mkdir -p /app/generated

# Expose port
EXPOSE 3030

# Run the service
CMD ["python", "code_generator.py"]
