#!/bin/sh
set -eu

OUT_FILE="${1:?out file required}"
CONTAINER_NAME="${2:-fastapi}"
INTERVAL_SECONDS="${3:-5}"

echo "timestamp,name,cpu_perc,mem_usage,mem_perc,net_io,block_io,pids" > "${OUT_FILE}"

while true; do
  TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  ROW="$(docker stats --no-stream --format "{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}},{{.NetIO}},{{.BlockIO}},{{.PIDs}}" "${CONTAINER_NAME}" 2>/dev/null || true)"

  if [ -n "${ROW}" ]; then
    echo "${TIMESTAMP},${ROW}" >> "${OUT_FILE}"
  fi

  sleep "${INTERVAL_SECONDS}"
done
