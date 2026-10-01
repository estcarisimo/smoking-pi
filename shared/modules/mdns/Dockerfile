# The .local name responder (main.py): the standard library only, on the
# host network, so it hears and answers the LAN's multicast DNS.
FROM python:3.14-slim

WORKDIR /app

COPY pyproject.toml ./
COPY *.py ./

RUN pip install --no-cache-dir . && mkdir -p /run/mdns

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "status.py", "--check"]

CMD ["python", "main.py"]
