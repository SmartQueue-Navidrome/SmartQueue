import random
import numpy as np
from locust import HttpUser, task, between


def make_payload(n_songs: int) -> dict:
    return {
        "user_features": np.random.rand(32).tolist(),
        "candidate_songs": [
            {"song_id": f"song_{random.randint(1, 10000)}", "features": np.random.rand(32).tolist()}
            for _ in range(n_songs)
        ],
    }


class SmartQueueUser(HttpUser):
    wait_time = between(1, 5)

    @task(10)
    def rank_typical(self):
        with self.client.post("/rank", json=make_payload(50), catch_response=True, name="/rank (50)") as r:
            if r.status_code == 200 and len(r.json()) == 50:
                r.success()
            else:
                r.failure(f"HTTP {r.status_code}")

    @task(3)
    def rank_small(self):
        with self.client.post("/rank", json=make_payload(10), catch_response=True, name="/rank (10)") as r:
            r.success() if r.status_code == 200 else r.failure(f"HTTP {r.status_code}")

    @task(1)
    def rank_large(self):
        with self.client.post("/rank", json=make_payload(200), catch_response=True, name="/rank (200)") as r:
            r.success() if r.status_code == 200 else r.failure(f"HTTP {r.status_code}")

    @task(1)
    def health(self):
        self.client.get("/health", name="/health")
