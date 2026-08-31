FROM python:3.11-slim

WORKDIR /app

# Build context is `shared/` (see editions/pro/docker-compose.yml), so the
# shared `common` package is reachable alongside this module's own source.
COPY modules/ai-insights/pyproject.toml ./
COPY modules/ai-insights/*.py ./
COPY modules/common/*.py ./common/

RUN pip install --no-cache-dir .

ENV REPORTS_DIR=/reports \
    REPORT_INTERVAL=86400

VOLUME ["/reports"]

# Loop mode: one report per REPORT_INTERVAL. Run `python main.py` (no flag)
# for a one-shot report.
CMD ["python", "main.py", "--loop"]
