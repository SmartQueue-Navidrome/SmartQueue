# SmartQueue Deployment Guide

Deploy the full SmartQueue ML-powered music recommendation system on Chameleon Cloud.

## Prerequisites

- Chameleon Cloud account with an active lease on KVM@TACC
- SSH key pair registered on Chameleon (`~/.ssh/id_rsa_chameleon`)
- Local tools: Terraform, Ansible, kubectl, Helm
- Chameleon S3 credentials (from CHI@TACC > Identity > Application Credentials > EC2)

## Quick Start

### Full deployment (from scratch)

```bash
# 1. Configure
cp .env.example .env                                         # fill in credentials
cp devops/tf/kvm/terraform.tfvars.example devops/tf/kvm/terraform.tfvars  # fill in lease details

# 2. Deploy everything
./deploy.sh
```

### Service deployment only (K8S cluster already running)

```bash
cp .env.example .env   # fill in credentials (including FLOATING_IP)
./deploy-services.sh
```

This deploys all application services onto an existing K8S cluster: builds Docker images, creates secrets, deploys platform services (PostgreSQL, MLflow, Redis, Navidrome), installs monitoring (Prometheus + Grafana), syncs serving environments via ArgoCD, starts the data generator, and registers Argo Workflows.

## What Gets Deployed

### Infrastructure (deploy.sh only)

| Component | Tool | Details |
|-----------|------|---------|
| 3 KVM instances | Terraform | node1 (control-plane + worker), node2/node3 (workers) |
| Private network | Terraform | 192.168.1.0/24, NAT via node1 |
| Block storage | Terraform + Ansible | 2x 5Gi volumes for PostgreSQL and Navidrome |
| K8S cluster | Kubespray | v1.30, Calico networking, containerd |
| ArgoCD | Helm (post_k8s) | GitOps deployment manager |
| Argo Workflows | Helm (post_k8s) | CI/CD/CT pipeline engine |
| Docker registry | post_k8s | node1:5000 (insecure, private network) |

### Services (deploy-services.sh)

| Service | Namespace | NodePort | Description |
|---------|-----------|----------|-------------|
| PostgreSQL | smartqueue-platform | - | Metadata backend for MLflow |
| MLflow | smartqueue-platform | 30500 | Experiment tracking & model registry |
| Redis | smartqueue-platform | - | Shared session store for serving pods |
| Navidrome | smartqueue-platform | 30453 | Music streaming service |
| Serving (prod) | smartqueue-prod | 30800 | LightGBM ranking model (2-8 replicas, HPA) |
| Serving (staging) | smartqueue-staging | 30801 | Staging environment |
| Serving (canary) | smartqueue-canary | 30802 | Canary environment |
| Generator | smartqueue-prod | - | Production traffic simulator |
| Prometheus | monitoring | 30090 | Metrics collection |
| Grafana | monitoring | 30300 | Dashboards (admin / smartqueue) |
| ArgoCD | argocd | 30443 | GitOps UI |
| Argo Workflows | argo | 30446 | Pipeline UI |

### Container Images

All images are built on node1 and pushed to the private registry (`node1:5000`):

| Image | Dockerfile | Purpose |
|-------|-----------|---------|
| smartqueue-mlflow | `devops/k8s/platform/mlflow/Dockerfile` | MLflow tracking server |
| smartqueue-serving | `serving/lightgbm_app/Dockerfile` | FastAPI + LightGBM serving |
| smartqueue-training | `training/docker/Dockerfile` | Model training (CT pipeline) |
| smartqueue-data | `data/pipelines/generator/Dockerfile` | Production traffic generator |
| smartqueue-retrain | `data/pipelines/pipeline2_retrain/Dockerfile` | Feedback data merge (CT pipeline) |

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `FLOATING_IP` | Yes (deploy-services.sh) | - | node1 public IP |
| `SSH_KEY` | No | `~/.ssh/id_rsa_chameleon` | SSH private key path |
| `PG_PASSWORD` | Yes | - | PostgreSQL password |
| `S3_ACCESS_KEY` | Yes | - | Chameleon S3 access key |
| `S3_SECRET_KEY` | Yes | - | Chameleon S3 secret key |
| `IMAGE_TAG` | No | `v2` | Docker image tag |

### Terraform Variables

Edit `devops/tf/kvm/terraform.tfvars`:

```hcl
cloud_name      = "KVM@TACC"
suffix          = "proj13"
key_pair        = "your-keypair-name"
reservation_id  = "your-chameleon-reservation-id"
image           = "CC-Ubuntu24.04"
flavor          = "m1.large"
node_count      = 3
```

## Troubleshooting

### Disk pressure on node1

node1 has ~60GB disk. Docker images accumulate quickly.

```bash
# Immediate fix
ssh cc@<FLOATING_IP> "docker builder prune -af && docker image prune -f"
```

A daily `disk-cleanup` CronJob runs automatically to prevent this.

### Serving pods CrashLoopBackOff

Common causes:
1. MLflow down (postgres evicted) -- fix disk pressure, restart postgres
2. Missing S3 credentials -- verify `s3-secret` exists
3. Model download timeout -- check MLflow connectivity

### Worker nodes can't pull images

Check containerd insecure registry config and NAT routing:
```bash
ssh node2 "curl -s http://node1:5000/v2/_catalog"
```

### Prometheus not scraping metrics

ServiceMonitors must use port **names** (e.g., `http`), not numbers (e.g., `"8000"`).
