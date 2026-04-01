#!/bin/bash
# Usage: HOST=http://<FLOATING_IP>:8000 bash run_evaluation.sh <option_name>
# Example: HOST=http://192.5.87.123:8000 bash run_evaluation.sh onnx_extended

set -e

OPTION=${1:-"baseline"}
HOST=${HOST:-"http://localhost:8000"}
OUT="results/${OPTION}"
mkdir -p "$OUT"

echo "=== Health check ==="
curl -sf "${HOST}/health"
echo ""

echo "=== Smoke test ==="
curl -sf -X POST "${HOST}/rank" \
     -H "Content-Type: application/json" \
     -d @../../shared/sample_input.json | python3 -m json.tool > "${OUT}/smoke_output.json"
echo "Saved: ${OUT}/smoke_output.json"

echo "=== Typical load (10 users, 5 min) ==="
locust -f locustfile.py --headless -u 10 -r 2 --run-time 5m \
       --host "$HOST" --csv "${OUT}/typical" --only-summary

echo "=== Peak load (30 users, 5 min) ==="
locust -f locustfile.py --headless -u 30 -r 5 --run-time 5m \
       --host "$HOST" --csv "${OUT}/peak" --only-summary

echo ""
echo "During peak load run this in a separate terminal:"
echo "  docker stats fastapi --no-stream >> ${OUT}/resource_usage.txt"
