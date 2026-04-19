#!/usr/bin/env python3
"""
Download the best trained LightGBM model from MLflow via HTTP — no S3 credentials needed.

Usage:
    # Download best model by a metric (default: ndcg_at_10, higher is better)
    python serving/scripts/download_model.py

    # Download specific run
    python serving/scripts/download_model.py --run-id 2ce32ba692c54095b4307ae8eb7ba508

    # Use a different metric to pick best run
    python serving/scripts/download_model.py --metric val_ndcg_at_10

Environment variables:
    MLFLOW_TRACKING_URI   MLflow server URL (default: http://129.114.24.226:30500)
    DEST_DIR              Where to save the model (default: ./model_artifacts)
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.parse

MLFLOW_URL = os.environ.get("MLFLOW_TRACKING_URI", "http://129.114.24.226:30500").rstrip("/")
EXPERIMENT_ID = "1"
DEFAULT_METRIC = "ndcg_at_10"


def mlflow_get(path, params=None):
    url = f"{MLFLOW_URL}/api/2.0/mlflow/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())


def get_best_run(metric):
    """Return the run_id with the highest value of metric across all runs."""
    data = mlflow_get("runs/search", {
        "experiment_ids": f'["{EXPERIMENT_ID}"]',
        "order_by": f'["metrics.{metric} DESC"]',
        "max_results": 1,
    })
    # runs/search uses POST — fall back to listing all runs and sorting
    return None


def get_best_run_post(metric):
    """POST to runs/search to find best run by metric."""
    import urllib.request
    url = f"{MLFLOW_URL}/api/2.0/mlflow/runs/search"
    payload = json.dumps({
        "experiment_ids": [EXPERIMENT_ID],
        "order_by": [f"metrics.{metric} DESC"],
        "max_results": 1,
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    runs = data.get("runs", [])
    if not runs:
        return None, None
    run = runs[0]
    run_id = run["info"]["run_id"]
    score = run.get("data", {}).get("metrics", {}).get(metric)
    return run_id, score


def list_artifact_files(run_id, path="model"):
    data = mlflow_get("artifacts/list", {"run_id": run_id, "path": path})
    return data.get("files", [])


def download_file(run_id, artifact_path, dest_path):
    url = f"{MLFLOW_URL}/get-artifact?run_uuid={run_id}&path={urllib.parse.quote(artifact_path)}"
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    urllib.request.urlretrieve(url, dest_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None, help="Specific MLflow run ID to download")
    parser.add_argument("--metric", default=DEFAULT_METRIC, help=f"Metric to pick best run (default: {DEFAULT_METRIC})")
    parser.add_argument("--dest", default=os.environ.get("DEST_DIR", "model_artifacts"))
    parser.add_argument("--mlflow-url", default=MLFLOW_URL)
    args = parser.parse_args()

    global MLFLOW_URL
    MLFLOW_URL = args.mlflow_url.rstrip("/")

    # Resolve run ID
    if args.run_id:
        run_id = args.run_id
        print(f"Using specified run: {run_id}")
    else:
        print(f"Finding best run by metric '{args.metric}' in experiment {EXPERIMENT_ID}...")
        run_id, score = get_best_run_post(args.metric)
        if run_id:
            print(f"Best run: {run_id}  ({args.metric}={score})")
        else:
            # Fall back to the known trained run
            run_id = "2ce32ba692c54095b4307ae8eb7ba508"
            print(f"Could not find run by metric — using default run: {run_id}")

    # List artifact files
    print(f"Listing artifacts for run {run_id}...")
    files = list_artifact_files(run_id, "model")
    if not files:
        print("ERROR: No files found under artifacts/model. Check the run ID.")
        sys.exit(1)

    print("Files found:")
    for f in files:
        print(f"  {f['path']}  ({f['file_size']} bytes)")

    # Find the model file (.lgb, .txt, .pkl, .joblib)
    model_file = None
    for f in files:
        p = f["path"]
        if p.endswith((".lgb", ".txt", ".pkl", ".joblib")):
            model_file = p
            break

    if not model_file:
        print("ERROR: No .lgb/.txt/.pkl file found in artifacts.")
        sys.exit(1)

    # Download
    dest_dir = os.path.abspath(args.dest)
    os.makedirs(dest_dir, exist_ok=True)

    ext = os.path.splitext(model_file)[1]
    canonical = os.path.join(dest_dir, "smartqueue_lgbm.txt" if ext in (".lgb", ".txt") else "ranking_model_latest.pkl")

    print(f"Downloading {model_file} -> {canonical}")
    download_file(run_id, model_file, canonical)

    size = os.path.getsize(canonical)
    print(f"Done. {canonical}  ({size/1024:.0f} KB)")
    print()
    print(f"Run ID saved: {run_id}")
    print(f"To start serving:")
    print(f"  MODEL_DIR={dest_dir} docker compose -f serving/docker/docker-compose-lightgbm.yaml up -d --build fastapi_lgbm")


if __name__ == "__main__":
    main()
