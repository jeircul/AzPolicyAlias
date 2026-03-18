# Multi-stage build for optimized image size
# Stage 1: Builder
FROM python:3.13-slim AS builder

WORKDIR /build

# Install uv for fast, deterministic installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Tell uv to create the venv at a known path for the COPY in stage 2
ENV UV_PROJECT_ENVIRONMENT=/.venv

# Copy project metadata only (avoids cache busting on src changes)
COPY pyproject.toml uv.lock ./

# Install runtime deps into an isolated venv — no dev deps, no editable install
RUN uv sync --frozen --no-dev --no-install-project

# Stage 2: Runtime
FROM python:3.13-slim

WORKDIR /app

# Install runtime dependencies and curl for healthcheck
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy virtual environment from builder
COPY --from=builder /.venv /.venv
ENV PATH="/.venv/bin:$PATH"

# Copy application code
COPY src/*.py ./
COPY src/static ./static/

# Create a non-root user with specific UID/GID
RUN groupadd -r -g 1000 app && \
    useradd -r -u 1000 -g app -M -s /sbin/nologin app && \
    chown -R app:app /app

# Switch to non-root user
USER app

# Expose port
EXPOSE 8000

# Add labels for metadata
LABEL maintainer="Azure Policy Aliases Viewer"
LABEL description="High-performance searchable interface for Azure Policy aliases"

# Health check
HEALTHCHECK --interval=30s \
    --timeout=10s \
    --start-period=40s \
    --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Set production environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=random

# Run the application with production settings
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--log-level", "info"]