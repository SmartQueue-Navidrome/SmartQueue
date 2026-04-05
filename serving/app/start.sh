#!/bin/sh
set -eu

exec uvicorn app:app \
  --host 0.0.0.0 \
  --port "${UVICORN_PORT:-8000}" \
  --workers "${UVICORN_WORKERS:-1}"
