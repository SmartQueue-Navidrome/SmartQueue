import os
from typing import List

import mlflow
import mlflow.lightgbm
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://129.114.25.107:8000")
MODEL_URI = os.environ.get("MODEL_URI", "runs:/b5bc4918ef0b41ff80844a52be538398/model")
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
model = mlflow.lightgbm.load_model(MODEL_URI)

app = FastAPI(title="SmartQueue LightGBM Serving", version=MODEL_VERSION)


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
        "model_uri": MODEL_URI,
        "tracking_uri": MLFLOW_TRACKING_URI,
    }


@app.post("/queue", response_model=QueueResponse)
def queue(req: QueueRequest):
    if not req.candidate_songs:
        raise HTTPException(status_code=422, detail="candidate_songs must not be empty")

    feature_frame = build_feature_frame(req)
    scores = model.predict(feature_frame)

    ranked = [
        RankedSong(video_id=song.video_id, engagement_probability=float(score), rank=0)
        for song, score in zip(req.candidate_songs, scores)
    ]
    ranked.sort(key=lambda item: item.engagement_probability, reverse=True)
    for idx, item in enumerate(ranked, start=1):
        item.rank = idx

    return QueueResponse(session_id=req.session_id, ranked_songs=ranked)


@app.post("/rank", response_model=QueueResponse)
def rank(req: QueueRequest):
    return queue(req)
