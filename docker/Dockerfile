# Production-ready Python backend
FROM python:3.12-slim

WORKDIR /app

# System deps (e.g. for psycopg2 if needed later)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[ingest]"

COPY app/ ./app/
COPY apps/ ./apps/
COPY connectors/ ./connectors/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/
COPY alembic.ini ./

# Non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
