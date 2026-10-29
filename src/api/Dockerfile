FROM python:3.12-slim

WORKDIR /app

# Copy requirements from root (build context must be root)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy full src code (so src.api and src.metrics work)
COPY src/ /app/src/

# Schema migrations (Alembic) — see agent_prompting/improvement_plan.md
# Section 2/6. Schema is no longer created implicitly at import time.
COPY alembic.ini /app/alembic.ini
COPY alembic/ /app/alembic/

# Set Python Path to include /app so "src.api.main" resolves
ENV PYTHONPATH=/app

# Expose Port
EXPOSE 8000

# Run pending migrations, then start the app.
CMD ["sh", "-c", "alembic upgrade head && uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload"]
