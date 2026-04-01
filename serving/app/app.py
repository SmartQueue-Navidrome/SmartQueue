import os
import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List

MODEL_PATH = os.environ.get("MODEL_PATH", "/app/model_artifacts/smartqueue_ranker.onnx")
MODEL_VERSION = os.environ.get("MODEL_VERSION", "1.0.0")

opts = ort.SessionOptions()
opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
session = ort.InferenceSession(MODEL_PATH, opts, providers=["CPUExecutionProvider"])
INPUT_NAME = session.get_inputs()[0].name

app = FastAPI(title="SmartQueue Ranking Service", version=MODEL_VERSION)


class CandidateSong(BaseModel):
    song_id: str
    features: List[float]


class RankRequest(BaseModel):
    user_features: List[float]
    candidate_songs: List[CandidateSong]


class RankedSong(BaseModel):
    song_id: str
    score: float


@app.get("/health")
def health():
    return {"status": "ok", "model_version": MODEL_VERSION}


@app.post("/rank", response_model=List[RankedSong])
def rank(req: RankRequest):
    if len(req.user_features) != 32:
        raise HTTPException(status_code=422, detail="user_features must have 32 elements")
    if not req.candidate_songs:
        raise HTTPException(status_code=422, detail="candidate_songs must not be empty")

    user_vec = np.array(req.user_features, dtype=np.float32)
    rows = []
    for song in req.candidate_songs:
        if len(song.features) != 32:
            raise HTTPException(status_code=422, detail=f"{song.song_id}: features must have 32 elements")
        rows.append(np.concatenate([user_vec, np.array(song.features, dtype=np.float32)]))

    scores = session.run(None, {INPUT_NAME: np.stack(rows)})[0]
    results = [RankedSong(song_id=s.song_id, score=float(sc)) for s, sc in zip(req.candidate_songs, scores)]
    results.sort(key=lambda r: r.score, reverse=True)
    return results
