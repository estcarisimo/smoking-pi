# SmokePing Web Admin with uv
# Modern Python web interface with Pydantic validation

FROM python:3.11-slim as builder

# Install system dependencies for building
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
RUN pip install uv

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml ./

# Install dependencies with uv (dependencies only, not as editable package)
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN uv pip install .[security]

# Production stage
FROM python:3.11-slim

# Install runtime dependencies (no Docker CLI: container management goes
# through the config-manager API, never the Docker socket)
RUN apt-get update && apt-get install -y \
    curl \
    dnsutils \
    iputils-ping \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create app user for security
RUN groupadd -r smokeping && useradd -r -g smokeping smokeping

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set working directory
WORKDIR /app

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p /app/config /app/logs /app/uploads /app/static/uploads \
    /tmp/tranco_cache /tmp/crux_cache /tmp/cloudflare_cache && \
    chown -R smokeping:smokeping /app /tmp/tranco_cache /tmp/crux_cache /tmp/cloudflare_cache

# Switch to non-root user
USER smokeping

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app
ENV FLASK_ENV=production
ENV CONFIG_DIR=/app/config
ENV PORT=8080
ENV HOST=0.0.0.0

# Health check: only an HTTP 200 from the unauthenticated /health endpoint
# passes (a redirect to /login must NOT count as healthy)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/health)" = "200" ] || exit 1

# Expose port
EXPOSE 8080

# Run with gunicorn for production
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-", "main:application"]