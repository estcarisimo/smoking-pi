FROM python:3.11-slim as builder

# Install system dependencies for building
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    docker.io \
    whois \
    libpq-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
RUN pip install uv

# Set working directory
WORKDIR /app

# Clone Netflix OCA Locator repository to /app/oca-locator (for direct usage)
RUN git clone https://github.com/estcarisimo/Netflix-OCA-Servers-Locator.git /app/oca-locator

# Install Netflix OCA Locator dependencies
WORKDIR /app/oca-locator
RUN pip install --no-cache-dir -e .

# Return to main app directory  
WORKDIR /app

# Copy dependency files
COPY pyproject.toml ./

# Install dependencies with uv
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN uv pip install .

# Production stage
FROM python:3.11-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    docker.io \
    whois \
    libpq5 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Clone Netflix OCA Locator repository to /app/oca-locator (for direct usage)
RUN git clone https://github.com/estcarisimo/Netflix-OCA-Servers-Locator.git /app/oca-locator

# Install Netflix OCA Locator dependencies
WORKDIR /app/oca-locator
RUN pip install --no-cache-dir -e .

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Return to main app directory
WORKDIR /app

# Copy application code
COPY config/ ./config/
COPY templates/ ./templates/
COPY scripts/ ./scripts/
COPY api.py ./
COPY wsgi.py ./
COPY models.py ./
COPY file_ops.py ./
COPY pydantic_models.py ./

# Make scripts executable
RUN chmod +x scripts/*.py

# Create output directory
RUN mkdir -p /app/output

# Set environment variables
ENV CONFIG_DIR=/app/config
ENV OUTPUT_DIR=/app/output
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Expose API port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Default command - REST API server via gunicorn (production WSGI server).
# wsgi.py runs the one-time startup initialization (bootstrap -> migration
# -> config generation) under a file lock so workers don't race.
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:5000", "--timeout", "120", "wsgi:app"]