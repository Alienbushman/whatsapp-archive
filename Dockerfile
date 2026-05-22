# ── Stage 1: build the React frontend into src/whatsapp_archive/static/ ──────
# Without this, the python stage just COPYs whatever bundle is committed to git,
# which silently drifts from the .jsx source whenever someone forgets to run
# `npm run build`. Build inside docker so the image always reflects the source
# tree, not the developer's last manual rebuild.
FROM node:20-alpine AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json frontend/
RUN cd frontend && npm ci
COPY frontend/ frontend/
# vite.config.js writes to ../src/whatsapp_archive/static — create that path
# so the build doesn't fail on a missing parent.
RUN mkdir -p src/whatsapp_archive/static
RUN cd frontend && npm run build
# Output is now at /app/src/whatsapp_archive/static/{index.html,assets/*}

# ── Stage 2: python runtime ──────────────────────────────────────────────────
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/
# Overlay the freshly-built frontend bundle on top of whatever was committed.
COPY --from=frontend-build /app/src/whatsapp_archive/static/ ./src/whatsapp_archive/static/

RUN pip install -e ".[dev]"

COPY docker_entrypoint.py ./
COPY tests/ ./tests/
COPY scripts/ ./scripts/

RUN mkdir -p /app/data/incoming

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=5 \
    CMD curl -fsS http://localhost:8000/api/chats || exit 1

CMD ["python", "docker_entrypoint.py"]
