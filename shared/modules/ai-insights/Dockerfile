FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY *.py ./

RUN pip install --no-cache-dir .

ENV REPORTS_DIR=/reports \
    REPORT_INTERVAL=86400

VOLUME ["/reports"]

# Loop mode: one report per REPORT_INTERVAL. Run `python main.py` (no flag)
# for a one-shot report.
CMD ["python", "main.py", "--loop"]
