#!/bin/bash
# Build and push custom images to private registry on node1
# Run from the SmartQueue repo root directory

set -e

REGISTRY="${1:-node1:5000}"

echo "=== Building and pushing images to ${REGISTRY} ==="

# MLflow server image
echo "[1/2] Building smartqueue-mlflow..."
docker build -t ${REGISTRY}/smartqueue-mlflow:v1 \
  -f devops/k8s/mlflow/Dockerfile \
  devops/k8s/mlflow/

echo "[1/2] Pushing smartqueue-mlflow..."
docker push ${REGISTRY}/smartqueue-mlflow:v1

# Serving image (FastAPI)
echo "[2/2] Building smartqueue-serving..."
docker build -t ${REGISTRY}/smartqueue-serving:v1 \
  -f serving/docker/Dockerfile.fastapi \
  serving/

echo "[2/2] Pushing smartqueue-serving..."
docker push ${REGISTRY}/smartqueue-serving:v1

echo ""
echo "=== All images pushed to ${REGISTRY} ==="
echo "  - ${REGISTRY}/smartqueue-mlflow:v1"
echo "  - ${REGISTRY}/smartqueue-serving:v1"
