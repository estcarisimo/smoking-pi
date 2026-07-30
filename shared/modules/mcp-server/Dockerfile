FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY *.py ./

RUN pip install --no-cache-dir .

ENV MCP_TRANSPORT=http \
    MCP_PORT=8090

EXPOSE 8090

CMD ["python", "main.py"]
