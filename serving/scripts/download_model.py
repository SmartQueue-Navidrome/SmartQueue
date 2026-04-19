#!/usr/bin/env python3
"""
Download the trained LightGBM model artifact from MLflow and save it
to a local directory so the serving container can load it from disk.

Usage (from repo root or serving/):
    # Basic — uses defaults hardcoded below
    python serving/scripts/download_model.py

    # Override destination
    python serving/scripts/download_model.py --dest serving/model_artifacts

    # Override MLflow URL
    MLFLOW_TRACKING_URI=http://129.114.24.226:30500 python serving/scripts/download_model.py

Environment variables (all optional — defaults point at the trained run):
    MLFLOW_TRACKING_URI        MLflow server URL (default: http://129.114.24.226:30500)
    MODEL_URI                  MLflow run/model URI (default: runs:/2ce32ba692c54095b4307ae8eb7ba508/model)
    MLFLOW_S3_ENDPOINT_URL     Chameleon S3 endpoint (default: https://chi.tacc.chameleoncloud.org:7480)
    AWS_ACCESS_KEY_ID          EC2 access key for Chameleon object storage
    AWS_SECRET_ACCESS_KEY      EC2 secret key for Chameleon object storage
    DEST_DIR                   Where to save the model (default: ./model_artifacts)
"""

import argparse
import os
import shutil
import sys

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://129.114.24.226:30500")
MODEL_URI = os.environ.get("MODEL_URI", "runs:/2ce32ba692c54095b4307ae8eb7ba508/model")
MLFLOW_S3_ENDPOINT_URL = os.environ.get(
    "MLFLOW_S3_ENDPOINT_URL", "https://chi.tacc.chameleoncloud.org:7480"
)


def main():
    parser = argparse.ArgumentParser(description="Download SmartQueue model from MLflow")
    parser.add_argument(
        "--dest",
        default=os.environ.get("DEST_DIR", "model_artifacts"),
        help="Destination directory for downloaded model artifacts (default: model_artifacts)",
    )
    parser.add_argument(
        "--tracking-uri",
        default=MLFLOW_TRACKING_URI,
        help=f"MLflow tracking URI (default: {MLFLOW_TRACKING_URI})",
    )
    parser.add_argument(
        "--model-uri",
        default=MODEL_URI,
        help=f"MLflow model URI (default: {MODEL_URI})",
    )
    args = parser.parse_args()

    # Set S3 endpoint so boto3 hits Chameleon storage, not AWS
    if MLFLOW_S3_ENDPOINT_URL:
        os.environ.setdefault("MLFLOW_S3_ENDPOINT_URL", MLFLOW_S3_ENDPOINT_URL)

    try:
        import mlflow
    except ImportError:
        print("ERROR: mlflow not installed. Run: pip install mlflow boto3")
        sys.exit(1)

    print(f"Connecting to MLflow at: {args.tracking_uri}")
    mlflow.set_tracking_uri(args.tracking_uri)

    dest = os.path.abspath(args.dest)
    os.makedirs(dest, exist_ok=True)

    print(f"Downloading model artifact from: {args.model_uri}")
    try:
        local_path = mlflow.artifacts.download_artifacts(args.model_uri, dst_path=dest)
    except Exception as e:
        print(f"ERROR: artifact download failed: {e}")
        print()
        print("Checklist:")
        print("  1. Is MLFLOW_TRACKING_URI reachable? curl " + args.tracking_uri + "/health")
        print("  2. Are AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY set?")
        print("     export AWS_ACCESS_KEY_ID=<key>")
        print("     export AWS_SECRET_ACCESS_KEY=<secret>")
        print("  3. Is MLFLOW_S3_ENDPOINT_URL correct?", MLFLOW_S3_ENDPOINT_URL)
        sys.exit(1)

    print(f"Downloaded to: {local_path}")

    # Look for the LightGBM model file inside the downloaded artifact tree
    # MLflow saves lgb models as model.pkl (joblib) or model.txt depending on version
    model_file = None
    for root, _, files in os.walk(local_path):
        for fname in files:
            if fname.endswith(".pkl") or fname.endswith(".txt") or fname.endswith(".joblib"):
                model_file = os.path.join(root, fname)
                break
        if model_file:
            break

    if model_file:
        # Copy to a canonical name the serving app expects
        ext = os.path.splitext(model_file)[1]
        if ext in (".pkl", ".joblib"):
            canonical = os.path.join(dest, "ranking_model_latest.pkl")
        else:
            canonical = os.path.join(dest, "smartqueue_lgbm.txt")
        shutil.copy2(model_file, canonical)
        print(f"Model file copied to: {canonical}")
        print()
        print("To start serving with this local model:")
        print(f"  MODEL_DIR={dest} docker compose -f serving/docker/docker-compose-lightgbm.yaml up -d --build fastapi_lgbm")
    else:
        print(f"WARNING: No .pkl/.txt/.joblib found under {local_path}")
        print("The full artifact directory was downloaded; inspect it manually.")
        print(f"  ls {local_path}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
