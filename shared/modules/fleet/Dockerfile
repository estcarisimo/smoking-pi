# Fleet (docs/fleet.md): one image, two entrypoints. The node agent runs
# `python -m fleet.agent` (profile fleet), the lead `python -m fleet.lead`
# (profile fleet-lead). Build context is `shared/` so the image can copy
# shared/modules/common.
#
# The package and common are copied as whole directories, never file by
# file: a file-by-file COPY hides a new module from every test until the
# image crash-loops (v2.13.5). tests/test_image.py fails when a module the
# entrypoints import is not under a COPY below. shared/.dockerignore keeps
# tests and caches out.
FROM python:3.15-slim

WORKDIR /app

# No dependencies yet: the standard library only (pyproject.toml says so).
COPY modules/common/ ./common/
COPY modules/fleet/fleet/ ./fleet/

RUN mkdir -p /var/lib/fleet && chown nobody:nogroup /var/lib/fleet

# Nothing here needs root. A named volume mounted at /var/lib/fleet takes
# this directory's owner when Docker creates it.
USER nobody
VOLUME ["/var/lib/fleet"]

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-m", "fleet.status", "--check"]

CMD ["python", "-m", "fleet.agent"]
