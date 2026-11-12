# --- 1. build the web interface ---
FROM node:22-alpine AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web ./
# vite.config.ts writes the build to ../src/cms_reporter/static
RUN npm run build

# --- 2. the application ---
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    CMS_DATA_DIR=/app/data \
    CMS_CONFIG=/app/config/network.yaml

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=web /build/src/cms_reporter/static ./src/cms_reporter/static
RUN pip install .

COPY config ./config

RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown -R app:app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:' + os.environ.get('PORT', '8000') + '/api/health')" || exit 1

# `docker run image <command>` runs any CLI command; with none, the web interface starts.
ENTRYPOINT ["cms-reporter"]
CMD ["serve", "--host", "0.0.0.0"]
