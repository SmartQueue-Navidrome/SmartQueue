# Ray Serve

This is a separate serving path for the bonus item. It uses the same request and response format as the current FastAPI app.

## Files

- `rayserve/app.py`: Ray Serve app with `/health`, `/queue`, and `/rank`
- `rayserve/run.py`: starts Ray Serve and deploys the app
- `rayserve/requirements.txt`: Python packages for the Ray Serve container
- `rayserve/start.sh`: container entrypoint
- `docker/Dockerfile.rayserve`: image build
- `docker/docker-compose-rayserve.yaml`: runs Ray Serve and the existing `eval` container together

## Step By Step

### 1. Go to the Docker folder on the Chameleon VM

```bash
cd SmartQueue/serving/docker
```

### 2. Start the Ray Serve container

This starts the service on port `8000` with the baseline ONNX model and 2 Ray Serve replicas.

```bash
MODEL_PATH=/app/model_artifacts/smartqueue_ranker.onnx MODEL_VERSION=rayserve_baseline RAY_SERVE_REPLICAS=2 docker compose -f docker-compose-rayserve.yaml up -d --build rayserve
```

### 3. Check that the container is running

```bash
docker ps
```

You should see a container named `rayserve`.

### 4. Run a health check from the VM shell

```bash
curl http://localhost:8000/health
```

Expected shape:

```json
{"status":"ok","model_version":"rayserve_baseline"}
```

### 5. Run a smoke test from the VM shell

```bash
curl -X POST http://localhost:8000/queue -H "Content-Type: application/json" -d @../../shared/sample_input.json
```

### 6. Optional: test from Jupyter

If your Jupyter container is already running, use a notebook cell like this:

```python
import requests, json

BASE_URL = "http://rayserve:8000"

print(requests.get(f"{BASE_URL}/health").json())

payload = {
    "session_id": "smoke_test_1",
    "user_features": {
        "user_skip_rate": 0.2,
        "user_favorite_genre_encoded": 12,
        "user_watch_time_avg": 140.0,
    },
    "candidate_songs": [
        {
            "video_id": "track_jazz_001",
            "release_year": 1998,
            "context_segment": 1,
            "genre_encoded": 12,
            "subgenre_encoded": 101,
        },
        {
            "video_id": "track_pop_042",
            "release_year": 2005,
            "context_segment": 1,
            "genre_encoded": 7,
            "subgenre_encoded": 88,
        },
    ],
}

r = requests.post(f"{BASE_URL}/queue", json=payload)
print(json.dumps(r.json(), indent=2))
```

### 7. Run the existing evaluation flow

From `serving/docker`:

```bash
MODEL_PATH=/app/model_artifacts/smartqueue_ranker.onnx MODEL_VERSION=rayserve_baseline RAY_SERVE_REPLICAS=2 docker compose --profile eval -f docker-compose-rayserve.yaml run --rm eval sh run_evaluation.sh rayserve_baseline
```

### 8. Find the results

Results will be written under:

```text
serving/evaluation/results/rayserve_baseline/
```

### 9. Stop the Ray Serve stack

```bash
docker compose -f docker-compose-rayserve.yaml down
```

## Notes

- Start and stop Ray Serve from the VM shell, not from inside the notebook.
- Use the notebook only to hit the running endpoint after the container is already up.
