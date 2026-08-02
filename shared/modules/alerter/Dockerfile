FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY *.py ./

RUN pip install --no-cache-dir . && mkdir -p /var/lib/alerter

ENV REPORTS_DIR=/reports

# Incident/dedup state survives restarts via this volume.
VOLUME ["/var/lib/alerter"]

# Loop mode (one evaluation per ALERT_INTERVAL). Run
# `python main.py --once` for a single test iteration.
CMD ["python", "main.py"]
