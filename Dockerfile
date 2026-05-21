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

RUN pip install -e ".[dev]"

COPY docker_entrypoint.py ./
COPY tests/ ./tests/
COPY scripts/ ./scripts/

RUN mkdir -p /app/data/incoming

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=5 \
    CMD curl -fsS http://localhost:8000/api/chats || exit 1

CMD ["python", "docker_entrypoint.py"]
