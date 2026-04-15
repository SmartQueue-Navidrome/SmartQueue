# SmartQueue — Production Flow

## Data Generator Logic

Processing flow for each session:

```
1. Read session_id from production.parquet

2. Compute user_features from first half of session:
   - user_skip_rate
   - user_favorite_genre_encoded
   - user_watch_time_avg

3. Sample 10 candidate songs from second half of session:
   - video_id, genre_encoded, subgenre_encoded, release_year, context_segment

4. POST /queue
   → Receive ranked_songs with engagement_probability
   → FastAPI automatically registers this session as active (no extra call needed)

5. Simulate engagement (wait 1s per song, record actual_is_engaged)

6. Write feedback JSONL locally + upload to S3

7. POST /session/end
   → FastAPI removes this session from active
```

---

## Required API Endpoints

### Existing (already in Serving)
```
POST /queue
  Request:
    {
      "session_id": "abc123",
      "user_features": {
        "user_skip_rate": 0.3,
        "user_favorite_genre_encoded": 5,
        "user_watch_time_avg": 45.2
      },
      "candidate_songs": [
        { "video_id": "v1", "genre_encoded": 3, ... },
        ...
      ]
    }

  Response:
    {
      "ranked_songs": [
        { "video_id": "v1", "rank": 1, "engagement_probability": 0.92 },
        ...
      ]
    }
```

### New (Serving needs to implement)
```
POST /session/end
  Request:  { "session_id": "abc123" }
  Response: { "ok": true }

GET /active-sessions
  Response:
    {
      "sessions": [
        {
          "session_id": "abc123",
          "user_features": { ... },
          "ranked_songs": [
            { "rank": 1, "video_id": "v1", "genre_encoded": 3, "engagement_probability": 0.92 },
            ...
          ]
        },
        ...
      ]
    }
```

---

## Navidrome UI

### Sidebar
```
▼ Albums
   All
   Random
   ✦ SmartQueue    ← new
   Favourites
   ...
```

### SmartQueue Page
```
SmartQueue — Live Sessions

● 8 active sessions

┌─────────────┬──────┬──────────────┬───────────────┬──────────┐
│ Session     │ Rank │ Video ID     │ Genre Encoded │ Score    │
├─────────────┼──────┼──────────────┼───────────────┼──────────┤
│ abc123...   │  1   │ v1           │ 3             │ 0.92     │
│             │  2   │ v2           │ 7             │ 0.87     │
│             │  3   │ v3           │ 1             │ 0.81     │
├─────────────┼──────┼──────────────┼───────────────┼──────────┤
│ def456...   │  1   │ v4           │ 5             │ 0.88     │
│             │  2   │ v5           │ 2             │ 0.76     │
│             │  3   │ v6           │ 9             │ 0.71     │
├─────────────┼──────┴──────────────┴───────────────┴──────────┤
│ ...         │  ...                                           │
└─────────────┴────────────────────────────────────────────────┘

auto-refresh every 3s
```

- Each session shows top 3 ranked songs
- Sessions disappear automatically when finished, new ones appear as they start
- Active session count shown in the top right
