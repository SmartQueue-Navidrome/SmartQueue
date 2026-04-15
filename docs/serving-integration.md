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
