# AdGuard Home is the DNS server; the supervisor (main.py) is the fallback
# around it. The binary is AdGuard's own static build, copied from the
# official image at a pinned version (bump adguard.SCHEMA_VERSION with it).
FROM adguard/adguardhome:v0.107.79 AS adguard

FROM python:3.14-slim

WORKDIR /app

COPY --from=adguard /opt/adguardhome/AdGuardHome /opt/adguardhome/AdGuardHome
COPY pyproject.toml ./
COPY *.py ./

RUN pip install --no-cache-dir . \
    && mkdir -p /opt/adguardhome/conf /opt/adguardhome/work /var/lib/dns-observer \
    && chmod 700 /opt/adguardhome/conf /opt/adguardhome/work

# conf: AdGuard's YAML (merged by the supervisor on each start).
# work: AdGuard's query log and statistics. state: status.json.
VOLUME ["/opt/adguardhome/conf", "/opt/adguardhome/work", "/var/lib/dns-observer"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "status.py", "--check"]

CMD ["python", "main.py"]
