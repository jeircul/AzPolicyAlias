# Multi-stage build for optimized image size
# Stage 1: Builder
FROM python:3.14-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install to a virtual environment
COPY requirements.txt .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime
FROM python:3.14-slim

WORKDIR /app

# Install runtime dependencies and curl for healthcheck
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY src/main.py src/azure_service.py ./
COPY src/static ./static/

# Create a non-root user with specific UID/GID
RUN groupadd -r -g 1000 app && \
    useradd -r -u 1000 -g app -m -s /bin/bash app && \
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