import os
import threading
import time
from datetime import datetime, timezone
from typing import List, Optional

import mlflow
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://129.114.25.107:8000")
MODEL_URI = os.environ.get("MODEL_URI", "runs:/b5bc4918ef0b41ff80844a52be538398/model")
MODEL_NAME = os.environ.get("MODEL_NAME", "smartqueue-ranking")
MODEL_STAGE = os.environ.get("MODEL_STAGE", "Production")
MODEL_VERSION = os.environ.get("MODEL_VERSION", "lightgbm_mlflow")

FEATURE_COLUMNS = [
    "release_year",
    "context_segment",
    "genre_encoded",
    "subgenre_encoded",
    "user_skip_rate",
    "user_favorite_genre_encoded",
    "user_watch_time_avg",
]

mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
active_model_uri = MODEL_URI
try:
    active_model_uri = f"models:/{MODEL_NAME}/{MODEL_STAGE}"
    model = mlflow.pyfunc.load_model(active_model_uri)
except Exception:
    # Backward-compatible fallback while registry wiring is in progress.
    active_model_uri = MODEL_URI
    model = mlflow.pyfunc.load_model(active_model_uri)

app = FastAPI(title="SmartQueue LightGBM Serving", version=MODEL_VERSION)
session_lock = threading.Lock()
active_sessions = {}

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "handler", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "handler"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)
ACTIVE_SESSIONS_GAUGE = Gauge(
    "smartqueue_active_sessions",
    "Number of active SmartQueue sessions",
)
PREDICTION_SCORE = Histogram(
    "prediction_score",
    "Distribution of model prediction scores",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)
PREDICTION_INVALID = Counter(
    "prediction_invalid_total",
    "Predictions outside [0,1] range",
)


class CandidateSong(BaseModel):
    video_id: str
    release_year: int
    context_segment: int
    genre_encoded: int
    subgenre_encoded: int


class UserFeatures(BaseModel):
    user_skip_rate: float
    user_favorite_genre_encoded: int
    user_watch_time_avg: float


class QueueRequest(BaseModel):
    session_id: str
    user_features: UserFeatures
    candidate_songs: List[CandidateSong]


class RankedSong(BaseModel):
    video_id: str
    engagement_probability: float
    rank: int


class QueueResponse(BaseModel):
    session_id: str
    ranked_songs: List[RankedSong]


class SessionStartRequest(BaseModel):
    session_id: str
    user_id: Optional[str] = None


class SessionEndRequest(BaseModel):
    session_id: str


class SessionResponse(BaseModel):
    session_id: str
    active: bool
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


class SessionActiveResponse(BaseModel):
    active_count: int
    sessions: List[str]


def build_feature_frame(req: QueueRequest) -> pd.DataFrame:
    rows = []
    for song in req.candidate_songs:
        rows.append(
            {
                "release_year": song.release_year,
                "context_segment": song.context_segment,
                "genre_encoded": song.genre_encoded,
                "subgenre_encoded": song.subgenre_encoded,
                "user_skip_rate": req.user_features.user_skip_rate,
                "user_favorite_genre_encoded": req.user_features.user_favorite_genre_encoded,
                "user_watch_time_avg": req.user_features.user_watch_time_avg,
            }
        )
    return pd.DataFrame(rows, columns=FEATURE_COLUMNS)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_version": MODEL_VERSION,
        "model_uri": active_model_uri,
        "model_name": MODEL_NAME,
        "model_stage": MODEL_STAGE,
        "tracking_uri": MLFLOW_TRACKING_URI,
    }


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/queue", response_model=QueueResponse)
def queue(req: QueueRequest):
    start_time = time.time()
    handler = "/queue"
    status = "200"
    try:
        if not req.candidate_songs:
            status = "422"
            raise HTTPException(status_code=422, detail="candidate_songs must not be empty")

        feature_frame = build_feature_frame(req)
        scores = model.predict(feature_frame)
        with session_lock:
            active_sessions[req.session_id] = {
                "user_id": None,
                "started_at": active_sessions.get(req.session_id, {}).get("started_at")
                or datetime.now(timezone.utc).isoformat(),
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
            }
            ACTIVE_SESSIONS_GAUGE.set(len(active_sessions))

        ranked = []
        for song, score in zip(req.candidate_songs, scores):
            score_val = float(score)
            PREDICTION_SCORE.observe(score_val)
            if score_val < 0 or score_val > 1:
                PREDICTION_INVALID.inc()
            ranked.append(
                RankedSong(video_id=song.video_id, engagement_probability=score_val, rank=0)
            )
        ranked.sort(key=lambda item: item.engagement_probability, reverse=True)
        for idx, item in enumerate(ranked, start=1):
            item.rank = idx

        return QueueResponse(session_id=req.session_id, ranked_songs=ranked)
    except HTTPException:
        raise
    except Exception:
        status = "500"
        raise
    finally:
        REQUEST_COUNT.labels(method="POST", handler=handler, status=status).inc()
        REQUEST_LATENCY.labels(method="POST", handler=handler).observe(time.time() - start_time)


@app.post("/rank", response_model=QueueResponse)
def rank(req: QueueRequest):
    return queue(req)


@app.post("/session/start", response_model=SessionResponse)
def session_start(req: SessionStartRequest):
    now = datetime.now(timezone.utc).isoformat()
    with session_lock:
        active_sessions[req.session_id] = {
            "user_id": req.user_id,
            "started_at": now,
            "last_seen_at": now,
        }
    return SessionResponse(session_id=req.session_id, active=True, started_at=now)


@app.post("/session/end", response_model=SessionResponse)
def session_end(req: SessionEndRequest):
    now = datetime.now(timezone.utc).isoformat()
    with session_lock:
        session = active_sessions.pop(req.session_id, None)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return SessionResponse(
        session_id=req.session_id,
        active=False,
        started_at=session.get("started_at"),
        ended_at=now,
    )


@app.get("/session/active", response_model=SessionActiveResponse)
def session_active():
    with session_lock:
        ids = sorted(active_sessions.keys())
    return SessionActiveResponse(active_count=len(ids), sessions=ids)
