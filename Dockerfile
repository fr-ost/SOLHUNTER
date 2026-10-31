# syntax=docker/dockerfile:1.7
# SolHunter - one image for every role (SERVICE_ROLE=all|api|scanner). Railway builds this automatically.

# ---- stage 1: build the web dashboard -------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- stage 2: python runtime ------------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY migrations ./migrations
COPY alembic.ini pyproject.toml ./
COPY scripts ./scripts
COPY --from=web /web/dist ./web/dist
RUN chmod +x scripts/*.sh && useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser
ENV SERVICE_ROLE=all PORT=8080
EXPOSE 8080
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1
CMD ["./scripts/start.sh"]
