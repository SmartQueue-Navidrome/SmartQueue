import random
import numpy as np
from locust import HttpUser, task, between


def make_payload(n_songs: int) -> dict:
    session_id = f"session_{random.randint(1, 100000)}"
    return {
        "session_id": session_id,
        "user_features": {
            "user_skip_rate": round(random.random(), 4),
            "user_favorite_genre_encoded": random.randint(0, 20),
            "user_watch_time_avg": round(random.uniform(10, 240), 2),
        },
        "candidate_songs": [
            {
                "video_id": f"video_{random.randint(1, 10000)}",
                "release_year": random.randint(1970, 2026),
                "context_segment": random.randint(0, 10),
                "genre_encoded": random.randint(0, 20),
                "subgenre_encoded": random.randint(0, 3000),
            }
            for _ in range(n_songs)
        ],
    }


class SmartQueueUser(HttpUser):
    wait_time = between(1, 5)

    @task(10)
    def rank_typical(self):
        with self.client.post("/queue", json=make_payload(50), catch_response=True, name="/queue (50)") as r:
            body = r.json() if r.status_code == 200 else {}
            ranked = body.get("ranked_songs", [])
            if r.status_code == 200 and len(ranked) == 50:
                r.success()
            else:
                r.failure(f"HTTP {r.status_code}")

    @task(3)
    def rank_small(self):
        with self.client.post("/queue", json=make_payload(10), catch_response=True, name="/queue (10)") as r:
            r.success() if r.status_code == 200 else r.failure(f"HTTP {r.status_code}")

    @task(1)
    def rank_large(self):
        with self.client.post("/queue", json=make_payload(200), catch_response=True, name="/queue (200)") as r:
            r.success() if r.status_code == 200 else r.failure(f"HTTP {r.status_code}")

    @task(1)
    def health(self):
        self.client.get("/health", name="/health")
