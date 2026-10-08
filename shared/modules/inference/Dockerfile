# Experimental detectors (main.py): persistent congestion with Jitterbug and
# loss degradation with Bayesian change points. Build context is `shared/`
# so the image can copy shared/modules/common (the Flux helpers).
#
# Python 3.13, not 3.14 like the other images: ruptures, which Jitterbug
# requires, publishes no 3.14 wheels yet, and building it from source would
# need a compiler in the image. torch comes from PyTorch's CPU-only index:
# the default one resolves CUDA builds, gigabytes the Pi cannot use.
FROM python:3.13-slim

# One thread for torch and the BLAS libraries: compose gives the container one
# core, and torch would otherwise start a thread per host core and be
# throttled, competing with the measurements.
ENV OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

WORKDIR /app

# The dependencies only (pyproject.toml's, with its `detect` extra), before
# the code: a code change then reuses this layer instead of reinstalling
# torch, and the module's files are not installed into site-packages.
RUN pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        "jitterbug-inference[bcp]>=2.3" "influxdb-client>=1.40" "numpy>=1.24" "pandas>=2.0" \
    && mkdir -p /var/lib/inference

COPY modules/inference/*.py ./
COPY modules/common/*.py ./common/

VOLUME ["/var/lib/inference"]

HEALTHCHECK --interval=300s --timeout=10s --start-period=600s --retries=3 \
    CMD ["python", "status.py", "--check"]

CMD ["python", "main.py"]
