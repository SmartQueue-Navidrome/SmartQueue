# Container Enumeration Table

All containers involved in each role, with links to Dockerfiles, Docker Compose files, and equivalent K8S manifests.

## Platform Services (DevOps/Platform Role)

| Container | Image | Dockerfile | Docker Compose | K8S Manifest |
|-----------|-------|-----------|----------------|--------------|
| Navidrome | `deluan/navidrome:0.53.3` | upstream | — | [k8s/navidrome/deployment.yaml](https://github.com/yanghao13111/SmartQueue/blob/main/devops/k8s/navidrome/deployment.yaml) |
| PostgreSQL | `postgres:16-alpine` | upstream | — | [k8s/postgres/statefulset.yaml](https://github.com/yanghao13111/SmartQueue/blob/main/devops/k8s/postgres/statefulset.yaml) |
| MLflow Tracking | `node1:5000/smartqueue-mlflow:v1` | [k8s/mlflow/Dockerfile](https://github.com/yanghao13111/SmartQueue/blob/main/devops/k8s/mlflow/Dockerfile) | — | [k8s/mlflow/deployment.yaml](https://github.com/yanghao13111/SmartQueue/blob/main/devops/k8s/mlflow/deployment.yaml) |
| Docker Registry | `registry:2` | upstream | — | Deployed via [Ansible post_k8s.yaml](https://github.com/yanghao13111/SmartQueue/blob/main/devops/ansible/playbooks/post_k8s.yaml), runs on node1:5000 |

> **Object Storage:** Uses Chameleon native S3 at CHI@TACC (`https://chi.tacc.chameleoncloud.org:7480`), not self-hosted MinIO. Browsable via Chameleon Horizon GUI.

## Training Role

| Container | Image | Dockerfile | Docker Compose | K8S Manifest |
|-----------|-------|-----------|----------------|--------------|
| Training | `node1:5000/smartqueue-training:v1` | [training/docker/Dockerfile](https://github.com/yanghao13111/SmartQueue/blob/main/training/docker/Dockerfile) | — | [k8s/training/job-template.yaml](https://github.com/yanghao13111/SmartQueue/blob/main/devops/k8s/training/job-template.yaml) |

## Serving Role

| Container | Image | Dockerfile | Docker Compose | K8S Manifest |
|-----------|-------|-----------|----------------|--------------|
| FastAPI Serving | `node1:5000/smartqueue-serving:v1` | [serving/docker/Dockerfile.fastapi](https://github.com/yanghao13111/SmartQueue/blob/main/serving/docker/Dockerfile.fastapi) | [serving/docker/docker-compose-system.yaml](https://github.com/yanghao13111/SmartQueue/blob/main/serving/docker/docker-compose-system.yaml) | [k8s/serving/deployment.yaml](https://github.com/yanghao13111/SmartQueue/blob/main/devops/k8s/serving/deployment.yaml) |
| Jupyter (Dev) | `quay.io/jupyter/base-notebook:python-3.11` | [serving/docker/Dockerfile.jupyter](https://github.com/yanghao13111/SmartQueue/blob/main/serving/docker/Dockerfile.jupyter) | [serving/docker/docker-compose-model.yaml](https://github.com/yanghao13111/SmartQueue/blob/main/serving/docker/docker-compose-model.yaml) | — (development only) |

## Data Role

| Container | Image | Dockerfile | Docker Compose | K8S Manifest |
|-----------|-------|-----------|----------------|--------------|
| Data Generator | `smartqueue-generator` | [data/pipelines/generator/Dockerfile](https://github.com/yanghao13111/SmartQueue/blob/main/data/pipelines/generator/Dockerfile) | [data/docker-compose.yml](https://github.com/yanghao13111/SmartQueue/blob/main/data/docker-compose.yml) | TBD (Milestone 2) |
| Pipeline 1 (Ingestion) | — | [data/pipelines/pipeline1_initial/Dockerfile](https://github.com/yanghao13111/SmartQueue/blob/main/data/pipelines/pipeline1_initial/Dockerfile) | runs on host (needs ~12GB RAM) | TBD (Milestone 2) |
| Pipeline 2 (Retrain) | `smartqueue-pipeline2` | [data/pipelines/pipeline2_retrain/Dockerfile](https://github.com/yanghao13111/SmartQueue/blob/main/data/pipelines/pipeline2_retrain/Dockerfile) | [data/docker-compose.yml](https://github.com/yanghao13111/SmartQueue/blob/main/data/docker-compose.yml) | [k8s/data/job-template.yaml](https://github.com/yanghao13111/SmartQueue/blob/main/devops/k8s/data/job-template.yaml) |
| Feature Service | `smartqueue-feature-service` | [data/pipelines/feature_service/Dockerfile](https://github.com/yanghao13111/SmartQueue/blob/main/data/pipelines/feature_service/Dockerfile) | [data/docker-compose.yml](https://github.com/yanghao13111/SmartQueue/blob/main/data/docker-compose.yml) | TBD (Milestone 2) |
