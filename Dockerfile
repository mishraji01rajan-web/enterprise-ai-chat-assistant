FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Minimal OS deps: build-essential covers any wheel that needs compiling
# (none of our pinned deps do on slim, but this keeps the image resilient to
# pip resolving a source dist on a given architecture).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY knowledge_base ./knowledge_base
COPY scripts ./scripts

ENV DATA_DIR=/app/data \
    SQL_DB_PATH=/app/data/app.db \
    VECTOR_DB_PATH=/app/data/chroma \
    ENVIRONMENT=production \
    LLM_PROVIDER=offline

# Create the runtime user BEFORE the build-time bootstrap step, and run that
# step AS that user. The local embedding model chromadb downloads on first
# use is cached under `$HOME/.cache` — if bootstrap ran as root (whose HOME
# is /root) but the container runs as a non-root user at start (whose HOME
# differs), the cache directory wouldn't match and the app would silently
# re-download the model on the first real request, stalling it for a long
# time (or failing outright with no network egress). Running both build-time
# and run-time as the same `appuser` guarantees the cache is actually reused.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

# Seed the SQL DB and ingest the knowledge base at BUILD time (not first
# container start). This both bakes in the demo dataset and pre-downloads/
# caches the local embedding model used for RAG, so the container doesn't
# need network egress on first run. When docker-compose mounts a fresh named
# volume at /app/data, Docker pre-populates it from this baked-in directory,
# so the running container still gets a fully seeded DB + vector store.
RUN python scripts/bootstrap.py

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["sh", "-c", "python scripts/bootstrap.py && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
