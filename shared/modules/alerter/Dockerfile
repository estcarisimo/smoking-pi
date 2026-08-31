FROM python:3.14-slim

WORKDIR /app

# Build context is `shared/` (see editions/pro/docker-compose.yml), so the
# shared `common` package is reachable alongside this module's own source.
COPY modules/alerter/pyproject.toml ./
COPY modules/alerter/*.py ./
COPY modules/common/*.py ./common/

RUN pip install --no-cache-dir . && mkdir -p /var/lib/alerter

ENV REPORTS_DIR=/reports

# Incident/dedup state survives restarts via this volume.
VOLUME ["/var/lib/alerter"]

# Loop mode (one evaluation per ALERT_INTERVAL). Run
# `python main.py --once` for a single test iteration.
CMD ["python", "main.py"]
