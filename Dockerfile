# Campaign Forge API: docker build -t campaign-forge-api . (see docs/API.md)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[api]"

# Run as an unprivileged user; runs and memory live on the /data volume.
RUN useradd --create-home --uid 10001 forge \
    && mkdir -p /data \
    && chown forge:forge /data
USER forge

ENV CF_RUN_STORE=sqlite \
    CF_MEMORY_DB=/data/campaign_forge.db \
    CF_RUNS_DIR=/data/runs

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "campaign_forge.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
