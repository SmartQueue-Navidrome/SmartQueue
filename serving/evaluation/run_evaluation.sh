#!/bin/sh
# Usage: HOST=http://<FLOATING_IP>:8000 bash run_evaluation.sh <option_name>
# Example: HOST=http://192.5.87.123:8000 bash run_evaluation.sh onnx_extended

set -e

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OPTION=${1:-"baseline"}
HOST=${HOST:-"http://localhost:8000"}
OUT="results/${OPTION}"
mkdir -p "$OUT"
SHARED_DIR=${SHARED_DIR:-"$SCRIPT_DIR/../../shared"}
TYPICAL_USERS=${TYPICAL_USERS:-10}
TYPICAL_SPAWN_RATE=${TYPICAL_SPAWN_RATE:-2}
TYPICAL_RUNTIME=${TYPICAL_RUNTIME:-5m}
PEAK_USERS=${PEAK_USERS:-30}
PEAK_SPAWN_RATE=${PEAK_SPAWN_RATE:-5}
PEAK_RUNTIME=${PEAK_RUNTIME:-5m}
CAPTURE_CONTAINER=${CAPTURE_CONTAINER:-}
CAPTURE_INTERVAL=${CAPTURE_INTERVAL:-5}
CAPTURE_PID=""

cleanup() {
  if [ -n "$CAPTURE_PID" ]; then
    kill "$CAPTURE_PID" 2>/dev/null || true
    wait "$CAPTURE_PID" 2>/dev/null || true
  fi
}

trap cleanup EXIT

if [ ! -f "${SHARED_DIR}/sample_input.json" ]; then
  echo "Missing sample input at ${SHARED_DIR}/sample_input.json" >&2
  exit 1
fi

export OUT OPTION HOST TYPICAL_USERS TYPICAL_SPAWN_RATE TYPICAL_RUNTIME
export PEAK_USERS PEAK_SPAWN_RATE PEAK_RUNTIME MODEL_PATH_USED MODEL_VERSION_USED UVICORN_WORKERS_USED

if [ -n "$CAPTURE_CONTAINER" ] && command -v docker >/dev/null 2>&1; then
  sh "$SCRIPT_DIR/capture_resources.sh" "${OUT}/resource_usage.csv" "$CAPTURE_CONTAINER" "$CAPTURE_INTERVAL" &
  CAPTURE_PID=$!
fi

echo "=== Health check ==="
curl -sf "${HOST}/health" | tee "${OUT}/health.json"
echo ""

python3 - <<'PY'
import json
import os
from pathlib import Path

out = Path(os.environ["OUT"])
meta = {
    "option": os.environ["OPTION"],
    "host": os.environ["HOST"],
    "model_path": os.environ.get("MODEL_PATH_USED", "unknown"),
    "model_version": os.environ.get("MODEL_VERSION_USED", "unknown"),
    "uvicorn_workers": os.environ.get("UVICORN_WORKERS_USED", "unknown"),
    "typical_users": os.environ.get("TYPICAL_USERS"),
    "typical_spawn_rate": os.environ.get("TYPICAL_SPAWN_RATE"),
    "typical_runtime": os.environ.get("TYPICAL_RUNTIME"),
    "peak_users": os.environ.get("PEAK_USERS"),
    "peak_spawn_rate": os.environ.get("PEAK_SPAWN_RATE"),
    "peak_runtime": os.environ.get("PEAK_RUNTIME"),
}
with open(out / "metadata.json", "w", encoding="utf-8") as f:
    json.dump(meta, f, indent=2)
PY

echo "=== Smoke test ==="
curl -sf -X POST "${HOST}/queue" \
     -H "Content-Type: application/json" \
     -d @"${SHARED_DIR}/sample_input.json" | python3 -m json.tool > "${OUT}/smoke_output.json"
echo "Saved: ${OUT}/smoke_output.json"

echo "=== Typical load (${TYPICAL_USERS} users, ${TYPICAL_RUNTIME}) ==="
locust -f locustfile.py --headless -u "${TYPICAL_USERS}" -r "${TYPICAL_SPAWN_RATE}" --run-time "${TYPICAL_RUNTIME}" \
       --host "$HOST" --csv "${OUT}/typical" --only-summary

echo "=== Peak load (${PEAK_USERS} users, ${PEAK_RUNTIME}) ==="
locust -f locustfile.py --headless -u "${PEAK_USERS}" -r "${PEAK_SPAWN_RATE}" --run-time "${PEAK_RUNTIME}" \
       --host "$HOST" --csv "${OUT}/peak" --only-summary
