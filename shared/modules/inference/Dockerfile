# Experimental detectors (main.py): persistent congestion with Jitterbug and
# loss degradation with Bayesian change points. Build context is `shared/`
# so the image can copy shared/modules/common (the Flux helpers).
#
# Python 3.13, not 3.14 like the other images: ruptures, which Jitterbug
# requires, publishes no 3.14 wheels yet, and building it from source would
# need a compiler in the image. torch comes from PyTorch's CPU-only index:
# the default one resolves CUDA builds, gigabytes the Pi cannot use.
FROM python:3.13-slim

WORKDIR /app

COPY modules/inference/pyproject.toml ./
COPY modules/inference/*.py ./
COPY modules/common/*.py ./common/

RUN pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        ".[detect]" \
    && mkdir -p /var/lib/inference

VOLUME ["/var/lib/inference"]

HEALTHCHECK --interval=300s --timeout=10s --start-period=600s --retries=3 \
    CMD ["python", "status.py", "--check"]

CMD ["python", "main.py"]
