# Container Enumeration Table

All containers involved in each role, with links to Dockerfiles, Docker Compose files, and equivalent K8S manifests.

## Platform Services (DevOps/Platform Role)

| Container | Image | Dockerfile | Docker Compose | K8S Manifest |
|-----------|-------|-----------|----------------|--------------|
| Navidrome | `deluan/navidrome:0.53.3` | upstream | — | [`k8s/navidrome/deployment.yaml`](../k8s/navidrome/deployment.yaml) |
| PostgreSQL | `postgres:16-alpine` | upstream | — | [`k8s/postgres/statefulset.yaml`](../k8s/postgres/statefulset.yaml) |
| MLflow Tracking | `node1:5000/smartqueue-mlflow:v1` | [`k8s/mlflow/Dockerfile`](../k8s/mlflow/Dockerfile) | — | [`k8s/mlflow/deployment.yaml`](../k8s/mlflow/deployment.yaml) |
| Docker Registry | `registry:2` | upstream | — | Deployed via Ansible (pre_k8s.yaml), runs on node1:5000 |

> **Object Storage:** Uses Chameleon native S3 at CHI@TACC (`https://chi.tacc.chameleoncloud.org:7480`), not self-hosted MinIO. Browsable via Chameleon Horizon GUI.

## Training Role

| Container | Image | Dockerfile | Docker Compose | K8S Manifest |
|-----------|-------|-----------|----------------|--------------|
| Training | `node1:5000/smartqueue-training:v1` | [`training/docker/Dockerfile`](../../training/docker/Dockerfile) | — | [`k8s/training/job-template.yaml`](../k8s/training/job-template.yaml) |

## Serving Role

| Container | Image | Dockerfile | Docker Compose | K8S Manifest |
|-----------|-------|-----------|----------------|--------------|
| FastAPI Serving | `node1:5000/smartqueue-serving:v1` | [`serving/docker/Dockerfile.fastapi`](../../serving/docker/Dockerfile.fastapi) | [`serving/docker/docker-compose-system.yaml`](../../serving/docker/docker-compose-system.yaml) | [`k8s/serving/deployment.yaml`](../k8s/serving/deployment.yaml) |
| Jupyter (Dev) | `quay.io/jupyter/base-notebook:python-3.11` | [`serving/docker/Dockerfile.jupyter`](../../serving/docker/Dockerfile.jupyter) | [`serving/docker/docker-compose-model.yaml`](../../serving/docker/docker-compose-model.yaml) | — (development only) |

## Data Role

| Container | Image | Dockerfile | Docker Compose | K8S Manifest |
|-----------|-------|-----------|----------------|--------------|
| Data Generator | `smartqueue-generator` | [`data/pipelines/generator/Dockerfile`](../../data/pipelines/generator/Dockerfile) | [`data/docker-compose.yml`](../../data/docker-compose.yml) | TBD (Milestone 2) |
| Pipeline 1 (Ingestion) | — | — | runs on host (needs ~12GB RAM) | TBD (Milestone 2) |
| Pipeline 2 (Retrain) | `smartqueue-pipeline2` | [`data/pipelines/pipeline2_retrain/Dockerfile`](../../data/pipelines/pipeline2_retrain/Dockerfile) | [`data/docker-compose.yml`](../../data/docker-compose.yml) | [`k8s/data/job-template.yaml`](../k8s/data/job-template.yaml) |
| Feature Service | `smartqueue-feature-service` | [`data/pipelines/feature_service/Dockerfile`](../../data/pipelines/feature_service/Dockerfile) | [`data/docker-compose.yml`](../../data/docker-compose.yml) | TBD (Milestone 2) |
