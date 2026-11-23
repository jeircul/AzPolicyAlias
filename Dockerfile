# Multi-stage build for optimized image size
# Stage 1: Builder — install deps via uv into an isolated venv
FROM python:3.14-slim AS builder

WORKDIR /build

# Bring in uv from the official distroless image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# uv will use this directory as the project venv
ENV UV_PROJECT_ENVIRONMENT=/.venv

# Install dependencies first (layer-cached unless lockfile changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Now copy source and do the final sync (installs the project itself)
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# Stage 2: Runtime — lean image with no build tooling
FROM python:3.14-slim

WORKDIR /app

# Install curl for healthcheck only
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the fully-populated venv from builder
COPY --from=builder /.venv /.venv
ENV PATH="/.venv/bin:$PATH"

# Copy application source
COPY src/*.py ./
COPY src/static ./static/

# Non-root user
RUN groupadd -r -g 1000 app && \
    useradd -r -u 1000 -g app -M -s /sbin/nologin app && \
    chown -R app:app /app

USER app

EXPOSE 8000

LABEL maintainer="Azure Policy Aliases Viewer"
LABEL description="High-performance searchable interface for Azure Policy aliases"

HEALTHCHECK --interval=30s \
    --timeout=10s \
    --start-period=40s \
    --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=random

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--log-level", "info"]
