#!/bin/sh
set -eu

ray start --head --disable-usage-stats --dashboard-host 0.0.0.0
exec python /app/rayserve/run.py
