#!/bin/bash
# Build and push custom images to private registry on node1
# Run from the SmartQueue repo root directory
# Usage: ./push-images.sh [registry] [tag]

set -e

REGISTRY="${1:-node1:5000}"
TAG="${2:-v1}"

echo "=== Building and pushing images to ${REGISTRY} (tag: ${TAG}) ==="

# MLflow server image
echo "[1/2] Building smartqueue-mlflow..."
docker build -t ${REGISTRY}/smartqueue-mlflow:${TAG} \
  -f devops/k8s/platform/mlflow/Dockerfile \
  devops/k8s/platform/mlflow/

echo "[1/2] Pushing smartqueue-mlflow..."
docker push ${REGISTRY}/smartqueue-mlflow:${TAG}

# Serving image (LightGBM FastAPI)
echo "[2/2] Building smartqueue-serving..."
docker build -t ${REGISTRY}/smartqueue-serving:${TAG} \
  -f serving/docker/Dockerfile.lightgbm \
  serving/

echo "[2/2] Pushing smartqueue-serving..."
docker push ${REGISTRY}/smartqueue-serving:${TAG}

echo ""
echo "=== All images pushed to ${REGISTRY} ==="
echo "  - ${REGISTRY}/smartqueue-mlflow:${TAG}"
echo "  - ${REGISTRY}/smartqueue-serving:${TAG}"
