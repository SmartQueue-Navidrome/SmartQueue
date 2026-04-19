import os
import json
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import List, Optional

import redis as redis_lib

import lightgbm as lgb
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Model loading config - priority: MOCK > local file(s) > MLflow (only if MLFLOW_TRACKING_URI set)
LOCAL_MODEL_PATH = os.environ.get("LOCAL_MODEL_PATH", "")
MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "").strip()
MODEL_URI = os.environ.get("MODEL_URI", "runs:/2ce32ba692c54095b4307ae8eb7ba508/model")
MODEL_NAME = os.environ.get("MODEL_NAME", "smartqueue-ranking")
MODEL_STAGE = os.environ.get("MODEL_STAGE", "Production")
MODEL_VERSION = os.environ.get("MODEL_VERSION", "lightgbm_v4")
MOCK_ON_MLFLOW_FAIL = os.environ.get("MOCK_ON_MLFLOW_FAIL", "false").lower() == "true"

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
    """Returns random scores — used when MOCK_MODE=true."""
    def predict(self, df):
        import random
        return [round(random.random(), 4) for _ in range(len(df))]


class _LightGBMWrapper:
    """Wrapper for native LightGBM model loaded from .txt file."""
    def __init__(self, model_path: str):
        self.booster = lgb.Booster(model_file=model_path)

    def predict(self, df):
        return self.booster.predict(df)


class _BoosterWrapper:
    """In-memory Booster (e.g. from joblib pickle saved by training)."""

    def __init__(self, booster: lgb.Booster):
        self.booster = booster

    def predict(self, df):
        return self.booster.predict(df)


def _iter_local_model_candidates() -> List[str]:
    paths: List[str] = []
    if LOCAL_MODEL_PATH:
        paths.append(LOCAL_MODEL_PATH)
    extra = os.environ.get("SMARTQUEUE_MODEL_PATHS", "")
    for p in extra.split(","):
        p = p.strip()
        if p:
            paths.append(p)
    for name in ("smartqueue_lgbm.txt", "ranking_model_latest.pkl"):
        paths.append(os.path.join("/models", name))
    seen = set()
    out: List[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _load_local_model(path: str):
    """Load from .txt (LightGBM native) or .pkl/.joblib (joblib dump of Booster)."""
    lower = path.lower()
    if lower.endswith(".pkl") or lower.endswith(".joblib"):
        import joblib

        obj = joblib.load(path)
        if isinstance(obj, lgb.Booster):
            return _BoosterWrapper(obj)
        raise RuntimeError(f"Pickle at {path} must contain a lightgbm.Booster, got {type(obj)}")
    return _LightGBMWrapper(path)


def _resolve_first_existing_local_path() -> Optional[str]:
    for p in _iter_local_model_candidates():
        if p and os.path.isfile(p):
            return p
    return None


active_model_uri = "none"
model = None

if MOCK_MODE:
    model = _MockModel()
    active_model_uri = "mock"
else:
    local_path = _resolve_first_existing_local_path()
    if local_path:
        print(f"[model] Loading from local file: {local_path}")
        model = _load_local_model(local_path)
        active_model_uri = f"local:{local_path}"
        print(f"[model] Loaded successfully from {local_path}")
    elif MLFLOW_TRACKING_URI:
        import mlflow

        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        try:
            active_model_uri = f"models:/{MODEL_NAME}/{MODEL_STAGE}"
            model = mlflow.pyfunc.load_model(active_model_uri)
            print(f"[model] Loaded from MLflow: {active_model_uri}")
        except Exception:
            try:
                active_model_uri = MODEL_URI
                model = mlflow.pyfunc.load_model(active_model_uri)
                print(f"[model] Loaded from MLflow fallback: {active_model_uri}")
            except Exception as e:
                print(f"[warn] MLflow load failed ({e}).")
                if MOCK_ON_MLFLOW_FAIL:
                    model = _MockModel()
                    active_model_uri = "mock-mlflow-fallback"
                    print("[model] MOCK_ON_MLFLOW_FAIL=true — using mock model.")
                else:
                    print(
                        "Fix: mount a model under /models (smartqueue_lgbm.txt or ranking_model_latest.pkl), "
                        "or set MLFLOW_S3_* creds + MLFLOW_TRACKING_URI, or MOCK_MODE=true."
                    )
                    raise
    elif MOCK_ON_MLFLOW_FAIL:
        model = _MockModel()
        active_model_uri = "mock-no-source"
        print("[model] No local model and MLflow disabled — MOCK_ON_MLFLOW_FAIL using mock.")
    else:
        searched = ", ".join(_iter_local_model_candidates()[:6])
        raise RuntimeError(
            "No model found. Mount MODEL_DIR so one of these exists: "
            f"{searched} ... "
            "Or set MLFLOW_TRACKING_URI (with S3 env vars for artifact download). "
            "Or MOCK_MODE=true."
        )

app = FastAPI(title="SmartQueue LightGBM Serving", version=MODEL_VERSION)

REDIS_HOST = os.environ.get("REDIS_HOST", "redis.smartqueue-platform.svc.cluster.local")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
SESSION_TTL_SECONDS = 300

_redis = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def _redis_get(session_id: str):
    raw = _redis.get(f"session:{session_id}")
    return json.loads(raw) if raw else None


def _redis_set(session_id: str, data: dict):
    _redis.setex(f"session:{session_id}", SESSION_TTL_SECONDS, json.dumps(data))


def _redis_delete(session_id: str):
    _redis.delete(f"session:{session_id}")


def _redis_all_sessions() -> dict:
    keys = _redis.keys("session:*")
    if not keys:
        return {}
    values = _redis.mget(keys)
    result = {}
    for key, val in zip(keys, values):
        if val:
            sid = key.removeprefix("session:")
            result[sid] = json.loads(val)
    return result

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
RERANK_TOTAL = Counter(
    "smartqueue_rerank_total",
    "Total rerank requests served",
)
FEEDBACK_SKIPS = Counter(
    "smartqueue_feedback_skips_total",
    "Songs skipped after reranking (user feedback)",
)
FEEDBACK_COMPLETIONS = Counter(
    "smartqueue_feedback_completions_total",
    "Songs played to completion after reranking (user feedback)",
)
FEEDBACK_SONGS_KEPT = Histogram(
    "smartqueue_feedback_songs_kept",
    "Fraction of ML-ranked top positions kept by user",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

# ─── Model-output drift monitoring ──────────────────────────────────────────
# Rolling window tracks recent prediction scores to detect distribution shifts.
# Prometheus Gauges expose mean/min/max so Grafana alerts can fire on drift.

DRIFT_WINDOW_SIZE = int(os.environ.get("DRIFT_WINDOW_SIZE", "500"))
_score_window: deque = deque(maxlen=DRIFT_WINDOW_SIZE)
_score_window_lock = threading.Lock()

PREDICTION_SCORE_MEAN = Gauge(
    "smartqueue_prediction_score_mean",
    "Rolling mean of prediction scores (drift monitoring)",
)
PREDICTION_SCORE_STDDEV = Gauge(
    "smartqueue_prediction_score_stddev",
    "Rolling std-dev of prediction scores (drift monitoring)",
)
PREDICTION_SCORE_MIN = Gauge(
    "smartqueue_prediction_score_min",
    "Rolling min of prediction scores (drift monitoring)",
)
PREDICTION_SCORE_MAX = Gauge(
    "smartqueue_prediction_score_max",
    "Rolling max of prediction scores (drift monitoring)",
)

# Per-feature input drift gauges (rolling mean of each feature)
FEATURE_DRIFT = Gauge(
    "smartqueue_feature_mean",
    "Rolling mean of input feature values (drift monitoring)",
    ["feature"],
)
_feature_windows: dict = {col: deque(maxlen=DRIFT_WINDOW_SIZE) for col in FEATURE_COLUMNS}
_feature_window_lock = threading.Lock()


def _update_drift_metrics(scores: list, feature_frame: "pd.DataFrame"):
    """Update rolling-window drift gauges for prediction scores and input features."""
    import math

    # Update score drift
    with _score_window_lock:
        _score_window.extend(scores)
        if _score_window:
            vals = list(_score_window)
            mean = sum(vals) / len(vals)
            PREDICTION_SCORE_MEAN.set(round(mean, 6))
            PREDICTION_SCORE_MIN.set(round(min(vals), 6))
            PREDICTION_SCORE_MAX.set(round(max(vals), 6))
            variance = sum((v - mean) ** 2 for v in vals) / len(vals)
            PREDICTION_SCORE_STDDEV.set(round(math.sqrt(variance), 6))

    # Update feature drift
    with _feature_window_lock:
        for col in FEATURE_COLUMNS:
            if col in feature_frame.columns:
                _feature_windows[col].extend(feature_frame[col].tolist())
                if _feature_windows[col]:
                    vals = list(_feature_windows[col])
                    FEATURE_DRIFT.labels(feature=col).set(
                        round(sum(vals) / len(vals), 6)
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
    model_version: str


class SessionEndRequest(BaseModel):
    session_id: str


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

        RERANK_TOTAL.inc()
        feature_frame = build_feature_frame(req)
        scores = model.predict(feature_frame)

        # Update drift monitoring gauges
        _update_drift_metrics([float(s) for s in scores], feature_frame)

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

        existing = _redis_get(req.session_id)
        existing_start = existing.get("started_at") if existing else None
        _redis_set(req.session_id, {
            "session_id": req.session_id,
            "user_features": {
                "user_skip_rate": req.user_features.user_skip_rate,
                "user_favorite_genre_encoded": req.user_features.user_favorite_genre_encoded,
                "user_watch_time_avg": req.user_features.user_watch_time_avg,
            },
            "ranked_songs": ranked_details,
            "started_at": existing_start or datetime.now(timezone.utc).isoformat(),
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
        })
        ACTIVE_SESSIONS_GAUGE.set(len(_redis.keys("session:*")))

        return QueueResponse(session_id=req.session_id, ranked_songs=ranked, model_version=MODEL_VERSION)
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



class SessionEndResponse(BaseModel):
    ok: bool


@app.post("/session/end", response_model=SessionEndResponse)
def session_end(req: SessionEndRequest):
    _redis_delete(req.session_id)
    ACTIVE_SESSIONS_GAUGE.set(_redis.dbsize())
    return SessionEndResponse(ok=True)


@app.get("/session/active", response_model=SessionActiveResponse)
def session_active():
    sessions = _redis_all_sessions()
    ids = sorted(sessions.keys())
    return SessionActiveResponse(active_count=len(ids), sessions=ids)


@app.get("/active-sessions", response_model=ActiveSessionsResponse)
def active_sessions_detailed():
    """Return detailed info for all active sessions (for Navidrome dashboard)."""
    sessions = _redis_all_sessions()
    session_list = []
    for sid in sorted(sessions.keys()):
        data = sessions[sid]
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


# ─── Feedback ────────────────────────────────────────────────────────────────

class SongFeedback(BaseModel):
    video_id: str
    action: str = Field(..., pattern="^(skip|complete)$")


class FeedbackRequest(BaseModel):
    session_id: str
    events: List[SongFeedback]
    final_order: Optional[List[str]] = None


class FeedbackResponse(BaseModel):
    session_id: str
    skips: int
    completions: int
    kept_ratio: Optional[float] = None


@app.post("/feedback", response_model=FeedbackResponse)
def feedback(req: FeedbackRequest):
    """
    Receive user engagement feedback after a reranked queue is played.
    - events: per-song skip/complete signals
    - final_order: the video_id order the user actually listened in,
      compared against the ML-ranked order to compute kept_ratio
    """
    start_time = time.time()
    handler = "/feedback"
    status = "200"
    try:
        skips = 0
        completions = 0
        for ev in req.events:
            if ev.action == "skip":
                skips += 1
                FEEDBACK_SKIPS.inc()
            else:
                completions += 1
                FEEDBACK_COMPLETIONS.inc()

        kept_ratio = None
        session_data = _redis_get(req.session_id)
        if session_data and req.final_order and session_data.get("ranked_songs"):
            ml_order = [s["video_id"] for s in session_data["ranked_songs"]]
            matches = sum(
                1 for ml_id, user_id in zip(ml_order, req.final_order)
                if ml_id == user_id
            )
            kept_ratio = matches / max(len(ml_order), 1)
            FEEDBACK_SONGS_KEPT.observe(kept_ratio)

        return FeedbackResponse(
            session_id=req.session_id,
            skips=skips,
            completions=completions,
            kept_ratio=kept_ratio,
        )
    except HTTPException:
        raise
    except Exception:
        status = "500"
        raise
    finally:
        REQUEST_COUNT.labels(method="POST", handler=handler, status=status).inc()
        REQUEST_LATENCY.labels(method="POST", handler=handler).observe(time.time() - start_time)
