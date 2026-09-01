FROM python:3.14-slim as builder

# Install system dependencies for building (git only needed here, in the
# builder — the production stage copies the results instead of re-cloning)
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    libpq-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
RUN pip install uv

# Set working directory
WORKDIR /app

# Create the virtual environment everything gets installed into
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Clone Netflix OCA Locator repository to /app/oca-locator and install it
# INTO THE VENV (the runtime invokes `python3 -m netflix_oca_locator`, and
# `python3` resolves to the venv interpreter via PATH)
RUN git clone https://github.com/estcarisimo/Netflix-OCA-Servers-Locator.git /app/oca-locator \
    && uv pip install /app/oca-locator

# Copy dependency files and install app dependencies into the same venv
COPY pyproject.toml ./
RUN uv pip install .

# Production stage
FROM python:3.14-slim

# Runtime OS dependencies only:
# - curl: image HEALTHCHECK
# - whois: OCA lookups
# - libpq5: psycopg2 runtime
# No git (nothing is cloned at runtime — the oca-locator checkout is copied
# from the builder). No docker package either: container control goes through
# the Python docker SDK over /var/run/docker.sock; nothing in this module
# shells out to a `docker` binary (and Debian bookworm has no cli-only
# `docker-cli` package — `docker.io` would pull in the entire engine).
RUN apt-get update && apt-get install -y \
    curl \
    whois \
    libpq5 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment and the oca-locator checkout from the builder
# (oca_fetcher.py verifies /app/oca-locator/src/netflix_oca_locator exists)
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/oca-locator /app/oca-locator
ENV PATH="/opt/venv/bin:$PATH"

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
