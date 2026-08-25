# ── Build stage ────────────────────────────────────────────────────────────────
# Installs all Python dependencies into /install so the runtime image is lean.
FROM python:3.12-slim AS builder

WORKDIR /app

# Build tools required by asyncpg (C extension) and cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Runtime lib needed by asyncpg at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application source — explicitly per-directory so .env / .venv are never
# baked into the image. Secrets must be injected at run time via env vars.
COPY alembic/       ./alembic/
COPY alembic.ini    .
COPY config.py      .
COPY database.py    .
COPY deps.py        .
COPY main.py        .
COPY models.py      .
COPY routers/       ./routers/
COPY services/      ./services/
COPY ws/            ./ws/

# Non-root user for security
RUN useradd -r -u 1001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# Run migrations using the full binary path — avoids the local ./alembic/
# directory shadowing the installed alembic package.
# Then start Uvicorn with a single worker (correct for async; scale by
# running multiple containers instead of multiple workers).
CMD ["sh", "-c", "\
    /usr/local/bin/alembic upgrade head && \
    uvicorn main:app \
      --host 0.0.0.0 \
      --port 8000 \
      --workers 1 \
      --no-access-log \
"]

