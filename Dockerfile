# Kestrel control room: one container with the React app, the governed API and a warehouse built
# from the synthetic raw/ files at image build time. Used by Render (render.yaml) and checked in CI.

# ---- 1. Web app --------------------------------------------------------------------------------
FROM node:22.11.0-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- 2. API and warehouse ----------------------------------------------------------------------
FROM python:3.13.0-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    KESTREL_HOST=0.0.0.0
WORKDIR /app

COPY pyproject.toml ./
COPY pipeline/ pipeline/
COPY mcp_server/ mcp_server/
COPY automation/ automation/
COPY evals/ evals/
COPY reporting/ reporting/
COPY replay/ replay/
COPY refresh/ refresh/
COPY api/ api/
RUN pip install -e .

COPY metrics/ metrics/
COPY raw/ raw/
# Build the warehouse now, so the container starts serving straight away.
RUN python -m pipeline.run

COPY --from=web /web/dist web/dist

# Run as an unprivileged user; it only needs to write the audit log and refresh state.
RUN useradd --create-home --uid 10001 kestrel \
 && mkdir -p logs state \
 && chown -R kestrel logs state
USER kestrel

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/api/health', timeout=4)"
CMD ["kestrel-app"]
