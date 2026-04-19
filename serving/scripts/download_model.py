#!/usr/bin/env python3
"""
Download the best trained LightGBM model from MLflow via HTTP — no S3 credentials needed.

Usage:
    # Download best model by metric (default: ndcg_at_10)
    python serving/scripts/download_model.py

    # Download a specific run
    python serving/scripts/download_model.py --run-id 2ce32ba692c54095b4307ae8eb7ba508

    # Use a different metric
    python serving/scripts/download_model.py --metric val_auc

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

DEFAULT_MLFLOW_URL = "http://129.114.24.226:30500"
DEFAULT_EXPERIMENT_ID = "1"
DEFAULT_METRIC = "ndcg_at_10"
DEFAULT_RUN_ID = "2ce32ba692c54095b4307ae8eb7ba508"


def api_get(mlflow_url, path, params=None):
    url = f"{mlflow_url}/api/2.0/mlflow/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())


def api_post(mlflow_url, path, payload):
    url = f"{mlflow_url}/api/2.0/mlflow/{path}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def get_best_run(mlflow_url, experiment_id, metric):
    """Return (run_id, metric_value) for the run with the highest metric value."""
    try:
        data = api_post(mlflow_url, "runs/search", {
            "experiment_ids": [experiment_id],
            "order_by": [f"metrics.{metric} DESC"],
            "max_results": 1,
        })
        runs = data.get("runs", [])
        if not runs:
            return None, None
        run = runs[0]
        run_id = run["info"]["run_id"]
        score = run.get("data", {}).get("metrics", {}).get(metric)
        return run_id, score
    except Exception as e:
        print(f"Warning: could not query best run ({e})")
        return None, None


def list_artifact_files(mlflow_url, run_id, path="model"):
    data = api_get(mlflow_url, "artifacts/list", {"run_id": run_id, "path": path})
    return data.get("files", [])


def download_file(mlflow_url, run_id, artifact_path, dest_path):
    url = f"{mlflow_url}/get-artifact?run_uuid={run_id}&path={urllib.parse.quote(artifact_path)}"
    urllib.request.urlretrieve(url, dest_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None, help="Specific MLflow run ID")
    parser.add_argument("--metric", default=DEFAULT_METRIC, help=f"Metric to pick best run (default: {DEFAULT_METRIC})")
    parser.add_argument("--dest", default=os.environ.get("DEST_DIR", "model_artifacts"))
    parser.add_argument("--mlflow-url", default=os.environ.get("MLFLOW_TRACKING_URI", DEFAULT_MLFLOW_URL))
    args = parser.parse_args()

    mlflow_url = args.mlflow_url.rstrip("/")

    # Resolve run ID
    if args.run_id:
        run_id = args.run_id
        print(f"Using specified run: {run_id}")
    else:
        print(f"Finding best run by metric '{args.metric}'...")
        run_id, score = get_best_run(mlflow_url, DEFAULT_EXPERIMENT_ID, args.metric)
        if run_id:
            print(f"Best run: {run_id}  ({args.metric}={score})")
        else:
            run_id = DEFAULT_RUN_ID
            print(f"Falling back to default run: {run_id}")

    # List artifact files
    print(f"Listing artifacts for run {run_id}...")
    files = list_artifact_files(mlflow_url, run_id, "model")
    if not files:
        print("ERROR: No files found under artifacts/model. Check the run ID.")
        sys.exit(1)

    print("Files found:")
    for f in files:
        print(f"  {f['path']}  ({f['file_size']} bytes)")

    # Find the model file
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
    canonical = os.path.join(
        dest_dir,
        "smartqueue_lgbm.txt" if ext in (".lgb", ".txt") else "ranking_model_latest.pkl"
    )

    print(f"Downloading {model_file} -> {canonical}")
    download_file(mlflow_url, run_id, model_file, canonical)

    size = os.path.getsize(canonical)
    print(f"Done. {canonical}  ({size/1024:.0f} KB)")
    print()
    print("To restart serving with the new model:")
    print(f"  MODEL_DIR={dest_dir} docker compose -f serving/docker/docker-compose-monitoring.yaml restart fastapi_lgbm")


if __name__ == "__main__":
    main()
