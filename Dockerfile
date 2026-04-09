FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if needed (e.g., for Pillow)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Default command matches your Procfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
