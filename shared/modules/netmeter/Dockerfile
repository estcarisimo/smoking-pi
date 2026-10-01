# The per-service traffic meter (main.py): nft from Debian, Python for the
# loop. Runs as root with CAP_NET_ADMIN only (compose drops every other
# capability): nft needs it in the host's network namespace, and nothing
# else here needs privilege.
FROM python:3.14-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends nftables \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY *.py ./

RUN pip install --no-cache-dir . && mkdir -p /var/lib/netmeter

VOLUME ["/var/lib/netmeter"]

HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "status.py", "--check"]

CMD ["python", "main.py"]
