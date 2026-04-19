# SmartQueue — Serving Integration Guide

This document describes what the serving team needs to implement to support the live session dashboard in Navidrome.

---

## Context

The data generator continuously processes sessions from `production.parquet`. For each session, it calls `POST /queue` to get ranked songs, simulates engagement, then notifies serving when the session is done.

Navidrome's SmartQueue page polls serving every 3 seconds to display which sessions are currently active and their ranked results.

---

## What Serving Already Has

```
POST /queue     → runs LightGBM inference, returns ranked_songs  ✅
GET  /health    → health check                                    ✅
```

---

## What Serving Needs to Add

### 1. In-memory active sessions state

Add a dict to track currently active sessions:

```python
from threading import Lock

active_sessions: dict = {}   # session_id → session data
sessions_lock = Lock()
```

### 2. Modify POST /queue to auto-register session

When `/queue` is called, automatically store the session and its results in `active_sessions`.

The generator passes `session_id` in every `/queue` request, so this info is already available.

What to store per session:
```python
active_sessions[session_id] = {
    "session_id": session_id,
    "user_features": {
        "user_skip_rate": ...,
        "user_favorite_genre_encoded": ...,
        "user_watch_time_avg": ...
    },
    "ranked_songs": [
        {
            "rank": 1,
            "video_id": "v1",
            "genre_encoded": 3,
            "engagement_probability": 0.92
        },
        ...
    ],
    "started_at": "2026-04-15T10:00:00Z"
}
```

Note: The generator passes `genre_encoded` as a candidate song feature to `/queue`. Serving should store it alongside `video_id`, `rank`, and `engagement_probability` so that the Navidrome UI can display all three columns (`video_id`, `genre_encoded`, `score`). Song metadata (title, artist) is not available at inference time and is not shown.

### 3. New endpoint: POST /session/end

Called by the generator when a session finishes (after feedback is written).

```
POST /session/end

Request:
  { "session_id": "abc123" }

Response:
  { "ok": true }

Behaviour:
  - Remove session_id from active_sessions
  - If session_id not found, still return { "ok": true } (idempotent)
```

### 4. New endpoint: GET /active-sessions

Polled by Navidrome every 3 seconds to update the live dashboard.

```
GET /active-sessions

Response:
  {
    "count": 8,
    "sessions": [
      {
        "session_id": "abc123",
        "user_features": {
          "user_skip_rate": 0.3,
          "user_favorite_genre_encoded": 5,
          "user_watch_time_avg": 45.2
        },
        "ranked_songs": [
          { "rank": 1, "video_id": "v1", "genre_encoded": 3, "engagement_probability": 0.92 },
          { "rank": 2, "video_id": "v2", "genre_encoded": 7, "engagement_probability": 0.87 },
          { "rank": 3, "video_id": "v3", "genre_encoded": 1, "engagement_probability": 0.81 }
        ],
        "started_at": "2026-04-15T10:00:00Z"
      },
      ...
    ]
  }
```

---

## Summary of Changes to app.py

| Change | Type | Details |
|--------|------|---------|
| Add `active_sessions` dict | New | In-memory state, thread-safe with Lock |
| Modify `POST /queue` | Modify | After ranking, write result to `active_sessions` |
| Add `POST /session/end` | New | Remove session from `active_sessions` |
| Add `GET /active-sessions` | New | Return current `active_sessions` for Navidrome |

---

## Notes

- `active_sessions` is in-memory only — it resets if the serving process restarts. This is acceptable since active sessions are ephemeral by nature.
- Thread safety: use a lock when reading/writing `active_sessions` since FastAPI handles requests concurrently.
- The generator calls `POST /session/end` after writing feedback to S3, so there may be a short delay between the session finishing and it disappearing from the dashboard.

---

## Serving Validation Tests

Run these from the VM (`cc@node1`) to confirm everything is working.

### 1. Health — confirm real model is loaded
```bash
curl -s http://129.114.24.226:8000/health | python3 -m json.tool
```
Expected: `"status": "ok"` and `"model_uri": "local:/models/smartqueue_lgbm.txt"`

### 2. Inference — confirm ranked songs are returned
```bash
curl -s -X POST http://129.114.24.226:8000/queue \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-1",
    "user_features": {"user_skip_rate": 0.2, "user_favorite_genre_encoded": 3, "user_watch_time_avg": 45.0},
    "candidate_songs": [
      {"video_id": "song1", "release_year": 2020, "context_segment": 1, "genre_encoded": 3, "subgenre_encoded": 10},
      {"video_id": "song2", "release_year": 2015, "context_segment": 2, "genre_encoded": 5, "subgenre_encoded": 20},
      {"video_id": "song3", "release_year": 2022, "context_segment": 1, "genre_encoded": 3, "subgenre_encoded": 15}
    ]
  }' | python3 -m json.tool
```
Expected: `ranked_songs` list ordered by `engagement_probability` descending.

### 3. Session tracking — confirm session was registered
```bash
curl -s http://129.114.24.226:8000/session/active | python3 -m json.tool
```
Expected: `"active_count": 1` with `test-1` in sessions list.

### 4. Active sessions detail — confirm full session data
```bash
curl -s http://129.114.24.226:8000/active-sessions | python3 -m json.tool
```
Expected: session with `user_features`, `ranked_songs`, and `started_at`.

### 5. Feedback — confirm skips/completions recorded
```bash
curl -s -X POST http://129.114.24.226:8000/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-1",
    "events": [
      {"video_id": "song2", "action": "complete"},
      {"video_id": "song3", "action": "skip"},
      {"video_id": "song1", "action": "complete"}
    ],
    "final_order": ["song2", "song1", "song3"]
  }' | python3 -m json.tool
```
Expected: `"skips": 1`, `"completions": 2`, and a `kept_ratio` value.

### 6. Prometheus metrics — confirm metrics are flowing
```bash
curl -s http://129.114.24.226:8000/metrics | grep smartqueue
```
Expected: `smartqueue_active_sessions`, `smartqueue_rerank_total`, `smartqueue_feedback_skips_total`, etc.

---

## Deploying / Updating the Model

### Download trained model from MLflow (no credentials needed)
```bash
cd ~/SmartQueue

# Download best model by metric (or falls back to default run)
python serving/scripts/download_model.py --dest serving/model_artifacts

# Download a specific run
python serving/scripts/download_model.py --run-id <run_id> --dest serving/model_artifacts
```

### Start serving stack
```bash
cd ~/SmartQueue/serving/docker
MODEL_DIR=~/SmartQueue/serving/model_artifacts \
docker compose -f docker-compose-monitoring.yaml up -d --build
```

### Restart serving after model update
```bash
cd ~/SmartQueue/serving/docker
MODEL_DIR=~/SmartQueue/serving/model_artifacts \
docker compose -f docker-compose-monitoring.yaml restart fastapi_lgbm
```

---

## Production Monitoring

### How it works (automatic — no manual steps needed)

Once the stack is running, monitoring is fully automatic:

| Component | What it does | How often |
|-----------|-------------|-----------|
| Prometheus | Scrapes `/metrics` from serving app | Every 15s |
| Grafana | Displays live dashboards from Prometheus data | Real-time |
| `smartqueue_monitor` | Checks thresholds, triggers rollback if breached | Every 60s |

You only open Grafana when you want to investigate something. Rollbacks happen automatically.

### Start the full monitoring stack
```bash
cd ~/SmartQueue/serving/docker
MODEL_DIR=~/SmartQueue/serving/model_artifacts \
docker compose -f docker-compose-monitoring.yaml --profile monitor up -d
```

### Access dashboards (via SSH tunnel from your local machine)
```bash
# Run on your LOCAL machine
ssh -i ~/.ssh/<your_key> -L 3000:localhost:3000 -L 9090:localhost:9090 cc@129.114.24.226
```
Then open:
- Grafana: `http://localhost:3000` (admin / admin)
- Prometheus: `http://localhost:9090`

In Grafana: Connections → Data Sources → Add → Prometheus → URL: `http://prometheus:9090`
Then import dashboard from `devops/k8s/monitoring/grafana-dashboards/serving.json`

### What is monitored

**Operational metrics** (from Prometheus):
- Request rate, error rate (4xx/5xx), p95 latency on `/queue`
- Active sessions count

**Model output metrics:**
- Average prediction score distribution
- Invalid prediction rate (scores outside [0, 1])

**User feedback metrics:**
- Skip rate, completion rate after reranking
- `kept_ratio` — fraction of ML-ranked top songs the user actually kept

### Rollback triggers (automatic)

The `smartqueue_monitor` container triggers a rollback if **any** of these are sustained:

| Condition | Threshold | Duration |
|-----------|-----------|----------|
| Error rate | > 2% | 5+ minutes |
| p95 latency | > 1200ms | 10+ minutes |
| Health check fails | 3 consecutive failures | immediate |

**Justification:** These thresholds are conservative for a music ranking feature — a bad model degrades user experience but isn't safety-critical. The 5–10 minute windows avoid false positives from traffic spikes.

### Canary promotion (before pushing new model to production)

When the training team produces a new model, run a 30-minute canary before promoting:

```bash
cd ~/SmartQueue/serving/monitoring
python promotion_triggers.py canary --duration 1800
```

Promotion criteria (all must pass over the 30-minute window):
- Error rate ≤ 1%
- p95 latency ≤ 800ms
- Invalid score rate ≤ 0.1%

### Manual commands

```bash
cd ~/SmartQueue/serving/monitoring

# Check current metrics
python promotion_triggers.py status

# Run canary evaluation (30 min)
python promotion_triggers.py canary --duration 1800

# Manual rollback
python promotion_triggers.py rollback

# Start continuous monitor manually
python promotion_triggers.py monitor --interval 60
```

### Check monitor container logs
```bash
docker logs smartqueue_monitor --tail 50 -f
```
