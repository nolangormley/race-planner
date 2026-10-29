FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

ENV PYTHONPATH=/app

# Expose Port for the API
EXPOSE 8000

# Run pending Alembic migrations, then start the API using uvicorn.
# Render will provide the port via the PORT environment variable.
CMD alembic upgrade head && uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
