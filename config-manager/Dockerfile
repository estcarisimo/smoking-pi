FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Clone Netflix OCA Locator repository to /app/oca-locator (for direct usage)
RUN git clone https://github.com/estcarisimo/Netflix-OCA-Servers-Locator.git /app/oca-locator

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY config/ ./config/
COPY templates/ ./templates/
COPY scripts/ ./scripts/
COPY test_*.py ./
COPY api.py ./

# Make scripts executable
RUN chmod +x scripts/*.py

# Create output directory
RUN mkdir -p /app/output

# Set environment variables
ENV CONFIG_DIR=/app/config
ENV OUTPUT_DIR=/app/output
ENV PYTHONPATH=/app

# Expose API port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Default command - REST API server
CMD ["python3", "api.py"]