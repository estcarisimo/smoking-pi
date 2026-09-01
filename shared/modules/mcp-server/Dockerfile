FROM python:3.14-slim

WORKDIR /app

# Build context is `shared/` (see editions/pro/docker-compose.yml), so the
# shared `common` package is reachable alongside this module's own source.
COPY modules/mcp-server/pyproject.toml ./
COPY modules/mcp-server/*.py ./
COPY modules/common/*.py ./common/

RUN pip install --no-cache-dir .

ENV MCP_TRANSPORT=http \
    MCP_PORT=8090

EXPOSE 8090

CMD ["python", "main.py"]
