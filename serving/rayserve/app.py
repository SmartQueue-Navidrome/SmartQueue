import os
from typing import List

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from ray import serve


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


def build_user_vector(user_features: UserFeatures) -> np.ndarray:
    user_vec = np.zeros(32, dtype=np.float32)
    user_vec[0] = user_features.user_skip_rate
    user_vec[1] = float(user_features.user_favorite_genre_encoded)
    user_vec[2] = user_features.user_watch_time_avg
    return user_vec


def build_song_vector(song: CandidateSong) -> np.ndarray:
    song_vec = np.zeros(32, dtype=np.float32)
    song_vec[0] = song.release_year
    song_vec[1] = song.context_segment
    song_vec[2] = song.genre_encoded
    song_vec[3] = song.subgenre_encoded
    return song_vec


app = FastAPI(title="SmartQueue Ray Serve", version=os.environ.get("MODEL_VERSION", "1.0.0-ray"))


@serve.deployment(ray_actor_options={"num_cpus": 1})
@serve.ingress(app)
class RankingService:
    def __init__(self):
        model_path = os.environ.get("MODEL_PATH", "/app/model_artifacts/smartqueue_ranker.onnx")
        self.model_version = os.environ.get("MODEL_VERSION", "1.0.0-ray")

        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
        self.session = ort.InferenceSession(model_path, opts, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def rank_request(self, req: QueueRequest) -> QueueResponse:
        user_vec = build_user_vector(req.user_features)
        rows = []
        for song in req.candidate_songs:
            rows.append(np.concatenate([user_vec, build_song_vector(song)]))

        scores = self.session.run(None, {self.input_name: np.stack(rows)})[0]
        ranked = [
            RankedSong(video_id=s.video_id, engagement_probability=float(sc), rank=0)
            for s, sc in zip(req.candidate_songs, scores)
        ]
        ranked.sort(key=lambda item: item.engagement_probability, reverse=True)
        for idx, item in enumerate(ranked, start=1):
            item.rank = idx

        return QueueResponse(session_id=req.session_id, ranked_songs=ranked)

    @app.get("/health")
    def health(self):
        return {"status": "ok", "model_version": self.model_version}

    @app.post("/queue", response_model=QueueResponse)
    def queue(self, req: QueueRequest):
        if not req.candidate_songs:
            raise HTTPException(status_code=422, detail="candidate_songs must not be empty")
        return self.rank_request(req)

    @app.post("/rank", response_model=QueueResponse)
    def rank(self, req: QueueRequest):
        return self.queue(req)
