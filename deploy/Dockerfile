# Production container for "Three Cities, One Pump" (NASA Space Apps 2026)
# Compatible with Hugging Face Spaces (Docker SDK), Render, Railway, Cloud Run & local demo.
#
# Build & run from repository root:
#   docker build -f deploy/Dockerfile -t sar-nasa-app .
#   docker run --rm -p 8501:8501 sar-nasa-app

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    PORT=8501

WORKDIR /app

# Install lightweight runtime dependencies first for layer caching
COPY requirements-app.txt ./
RUN python -m venv /app/.venv \
    && /app/.venv/bin/pip install --upgrade pip \
    && /app/.venv/bin/pip install -r requirements-app.txt

# Copy application code, config and pre-computed <15 MiB processed artefacts
COPY config ./config
COPY examples ./examples
COPY scripts ./scripts
COPY src ./src
COPY data/processed ./data/processed
COPY README.md ./README.md

# Validate contract & artefacts at image build time (fail-closed deploy)
RUN /app/.venv/bin/python -m src.domain.contracts \
    && /app/.venv/bin/python -m src.data.validation --processed data/processed --require-cases

# Copy Streamlit entry point + optional theme config once Rol 2 lands app.py
COPY . ./

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c "import os, urllib.request; p = os.environ.get('PORT', '8501'); urllib.request.urlopen(f'http://127.0.0.1:{p}/_stcore/health', timeout=4)" || exit 1

CMD ["/bin/sh", "-c", "/app/.venv/bin/streamlit run app.py --server.address=0.0.0.0 --server.port=${PORT:-8501}"]
