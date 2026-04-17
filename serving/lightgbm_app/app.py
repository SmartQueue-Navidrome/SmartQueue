import os
import threading
import time
from datetime import datetime, timezone
from typing import List, Optional

import mlflow
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://129.114.25.107:8000")
MODEL_URI = os.environ.get("MODEL_URI", "models:/smartqueue-ranking/Production")
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

MOCK_MODE = os.environ.get("MOCK_MODE", "false").lower() == "true"

class _MockModel:
    """Returns random scores — used when MOCK_MODE=true or MLflow is unreachable."""
    def predict(self, df):
        import random
        return [round(random.random(), 4) for _ in range(len(df))]

mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
active_model_uri = MODEL_URI
model = None

if MOCK_MODE:
    model = _MockModel()
    active_model_uri = "mock"
else:
    try:
        active_model_uri = f"models:/{MODEL_NAME}/{MODEL_STAGE}"
        model = mlflow.pyfunc.load_model(active_model_uri)
    except Exception:
        try:
            active_model_uri = MODEL_URI
            model = mlflow.pyfunc.load_model(active_model_uri)
        except Exception as e:
            print(f"[warn] MLflow unavailable ({e}). Set MOCK_MODE=true to run without MLflow.")
            raise

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
INVALID_REQUEST_COUNT = Counter(
    "invalid_request_total",
    "Requests rejected due to invalid input (422)",
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    INVALID_REQUEST_COUNT.inc()
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


class CandidateSong(BaseModel):
    video_id: str
    release_year: int = Field(..., ge=1900, le=2030)
    context_segment: int = Field(..., ge=0)
    genre_encoded: int = Field(..., ge=0, le=50)
    subgenre_encoded: int = Field(..., ge=0, le=300)


class UserFeatures(BaseModel):
    user_skip_rate: float = Field(..., ge=0.0, le=1.0)
    user_favorite_genre_encoded: int = Field(..., ge=0, le=50)
    user_watch_time_avg: float = Field(..., ge=0.0)


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


class RankedSongDetail(BaseModel):
    rank: int
    video_id: str
    genre_encoded: int
    engagement_probability: float


class SessionDetail(BaseModel):
    session_id: str
    user_features: UserFeatures
    ranked_songs: List[RankedSongDetail]
    started_at: str


class ActiveSessionsResponse(BaseModel):
    count: int
    sessions: List[SessionDetail]


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

        ranked = []
        ranked_details = []
        for song, score in zip(req.candidate_songs, scores):
            score_val = float(score)
            PREDICTION_SCORE.observe(score_val)
            if score_val < 0 or score_val > 1:
                PREDICTION_INVALID.inc()
            ranked.append(
                RankedSong(video_id=song.video_id, engagement_probability=score_val, rank=0)
            )
            ranked_details.append({
                "video_id": song.video_id,
                "genre_encoded": song.genre_encoded,
                "engagement_probability": score_val,
                "rank": 0,
            })
        ranked.sort(key=lambda item: item.engagement_probability, reverse=True)
        ranked_details.sort(key=lambda item: item["engagement_probability"], reverse=True)
        for idx, (item, detail) in enumerate(zip(ranked, ranked_details), start=1):
            item.rank = idx
            detail["rank"] = idx

        with session_lock:
            existing_start = active_sessions.get(req.session_id, {}).get("started_at")
            active_sessions[req.session_id] = {
                "session_id": req.session_id,
                "user_features": {
                    "user_skip_rate": req.user_features.user_skip_rate,
                    "user_favorite_genre_encoded": req.user_features.user_favorite_genre_encoded,
                    "user_watch_time_avg": req.user_features.user_watch_time_avg,
                },
                "ranked_songs": ranked_details,
                "started_at": existing_start or datetime.now(timezone.utc).isoformat(),
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
            }
            ACTIVE_SESSIONS_GAUGE.set(len(active_sessions))

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


class SessionEndResponse(BaseModel):
    ok: bool


@app.post("/session/end", response_model=SessionEndResponse)
def session_end(req: SessionEndRequest):
    with session_lock:
        active_sessions.pop(req.session_id, None)
        ACTIVE_SESSIONS_GAUGE.set(len(active_sessions))
    return SessionEndResponse(ok=True)


@app.get("/session/active", response_model=SessionActiveResponse)
def session_active():
    with session_lock:
        ids = sorted(active_sessions.keys())
    return SessionActiveResponse(active_count=len(ids), sessions=ids)


@app.get("/active-sessions", response_model=ActiveSessionsResponse)
def active_sessions_detailed():
    """Return detailed info for all active sessions (for Navidrome dashboard)."""
    with session_lock:
        session_list = []
        for sid in sorted(active_sessions.keys()):
            data = active_sessions[sid]
            session_list.append(
                SessionDetail(
                    session_id=sid,
                    user_features=UserFeatures(
                        user_skip_rate=data["user_features"]["user_skip_rate"],
                        user_favorite_genre_encoded=data["user_features"]["user_favorite_genre_encoded"],
                        user_watch_time_avg=data["user_features"]["user_watch_time_avg"],
                    ),
                    ranked_songs=[
                        RankedSongDetail(
                            rank=rs["rank"],
                            video_id=rs["video_id"],
                            genre_encoded=rs["genre_encoded"],
                            engagement_probability=rs["engagement_probability"],
                        )
                        for rs in data["ranked_songs"]
                    ],
                    started_at=data["started_at"],
                )
            )
    return ActiveSessionsResponse(count=len(session_list), sessions=session_list)
