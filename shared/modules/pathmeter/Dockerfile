# The path meter (main.py): scamper from Debian for Paris traceroute, Python
# for the loop. Build context is `shared/` so the image can copy
# shared/modules/common (the Flux helpers and the AS lookup).
#
# Compose drops every capability but four: NET_RAW for scamper's raw
# sockets, and SYS_CHROOT, SETUID and SETGID for scamper's own privilege
# separation, which chroots the process that parses replies into /var/empty
# and runs it as nobody. scamper has no switch to skip that, and it is the
# part that reads what the network sends back, so it is kept.
FROM python:3.15-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends scamper \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p -m 0555 /var/empty

WORKDIR /app

# The dependencies only (pyproject.toml's), before the code: a code change
# then reuses this layer, and the module's files are not installed into
# site-packages.
# originet: the IP-to-AS table (a Rust wheel, aarch64 and x86_64).
RUN pip install --no-cache-dir "influxdb-client>=1.40" "dnspython>=2.6" "originet>=1.0.1,<2" \
    && mkdir -p /var/lib/pathmeter

COPY modules/pathmeter/*.py ./
COPY modules/common/*.py ./common/
# The AS roles classify() reads (asn_roles.csv) and their license.
COPY modules/common/data/ ./common/data/

VOLUME ["/var/lib/pathmeter"]

HEALTHCHECK --interval=300s --timeout=10s --start-period=600s --retries=3 \
    CMD ["python", "status.py", "--check"]

CMD ["python", "main.py"]
