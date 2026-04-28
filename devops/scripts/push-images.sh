#!/bin/bash
# Build and push ALL custom images to private registry on node1.
# Run from the SmartQueue repo root directory ON node1.
#
# Usage: sudo bash devops/scripts/push-images.sh [registry]
#
# After each build+push, runs docker prune to avoid disk pressure
# (node1 root partition is only 37GB; build cache can fill it).

set -e

REGISTRY="${1:-node1:5000}"

echo "=== Building and pushing all images to ${REGISTRY} ==="

build_and_push() {
    local name="$1"
    local tag="$2"
    local dockerfile="$3"
    local context="$4"

    echo ""
    echo "--- Building ${name}:${tag} ---"
    docker build -t "${REGISTRY}/${name}:${tag}" -f "${dockerfile}" "${context}"
    docker push "${REGISTRY}/${name}:${tag}"

    # Prune build cache after each image to keep disk usage low
    docker builder prune -af --filter 'until=1h' 2>/dev/null || true
    echo "--- ${name}:${tag} pushed ---"
}

# 1. MLflow server
build_and_push "smartqueue-mlflow" "v1" \
    "devops/k8s/platform/mlflow/Dockerfile" \
    "devops/k8s/platform/mlflow/"

# 2. Serving (LightGBM FastAPI)
build_and_push "smartqueue-serving" "v3" \
    "serving/docker/Dockerfile.lightgbm" \
    "serving/"

# 3. Navidrome (custom fork with SmartQueue integration)
# On node1: ~/navidrome/ (sibling of ~/SmartQueue/)
# Locally: ../navidrome/ (sibling of SmartQueue/)
NAVIDROME_DIR=""
for candidate in "$HOME/navidrome" "../navidrome"; do
    if [ -f "$candidate/Dockerfile" ]; then
        NAVIDROME_DIR="$candidate"
        break
    fi
done

if [ -n "$NAVIDROME_DIR" ]; then
    build_and_push "smartqueue-navidrome" "v2" \
        "$NAVIDROME_DIR/Dockerfile" \
        "$NAVIDROME_DIR/"
else
    echo "SKIP: navidrome/Dockerfile not found (tried ~/navidrome/ and ../navidrome/)"
fi

# 4. Training
build_and_push "smartqueue-training" "v2" \
    "training/docker/Dockerfile" \
    "training/"

# 5. Retrain pipeline
build_and_push "smartqueue-retrain" "v2" \
    "data/pipelines/pipeline2_retrain/Dockerfile" \
    "data/pipelines/pipeline2_retrain/"

# 6. Data generator
build_and_push "smartqueue-data" "v4" \
    "data/pipelines/generator/Dockerfile" \
    "data/pipelines/generator/"

# Final cleanup
echo ""
echo "--- Final prune ---"
docker builder prune -af 2>/dev/null || true
docker image prune -f 2>/dev/null || true

echo ""
echo "=== All images pushed to ${REGISTRY} ==="
docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}" | grep "${REGISTRY}" || true
