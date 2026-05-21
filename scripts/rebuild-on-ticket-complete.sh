#!/usr/bin/env bash
# Triggered by the Claude Code PostToolUse hook on mcp__memories__ticket_complete.
# Rebuilds the React bundle (if frontend/ exists), the web Docker image, and
# brings up the web service. Idempotent — safe to run when nothing changed.
set -e

# Locate the project root from this script's path so the hook can call us with
# whatever cwd Claude Code happens to use.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "[rebuild] cwd=$ROOT"

if [ -d frontend ]; then
  echo "[rebuild] frontend bundle…"
  (cd frontend && npm run build)
fi

echo "[rebuild] docker compose build web…"
docker compose build web

echo "[rebuild] docker compose up -d --force-recreate web…"
# --force-recreate guarantees the running container is replaced even when compose
# thinks the spec hasn't changed. Without this, code changes can land in the image
# but uvicorn keeps serving the old module loaded into memory on container start
# (root cause of bug #34, observed 2026-05-19).
docker compose up -d --force-recreate web

echo "[rebuild] done."
