# SmartQueue Deployment Guide

Deploy the full SmartQueue ML-powered music recommendation system on Chameleon Cloud.

## Prerequisites

- Chameleon Cloud account with an active lease on KVM@TACC (3x m1.large)
- SSH key pair registered on Chameleon (`~/.ssh/id_rsa_chameleon`)
- Local tools: `terraform`, `ansible`, `ssh`
- `~/.config/openstack/clouds.yaml` with Chameleon application credentials
- Chameleon S3 credentials (from CHI@TACC > Identity > Application Credentials > EC2)

## Quick Start

```bash
cd SmartQueue/devops

# 1. Create secrets file (one time)
cat > .secrets << 'EOF'
POSTGRES_USER=mlflow
POSTGRES_PASSWORD=<your-postgres-password>
POSTGRES_DB=mlflow
S3_ACCESS_KEY=<your-chameleon-s3-access-key>
S3_SECRET_KEY=<your-chameleon-s3-secret-key>
EOF

# 2. Deploy everything (~30-40 min)
bash scripts/deploy.sh <RESERVATION_ID>
```

That's it. One command provisions VMs, deploys K8S, and brings up all services.

### Run individual phases

```bash
bash scripts/deploy.sh <RESERVATION_ID> --phase terraform   # Phase 1: provision VMs
bash scripts/deploy.sh --floating-ip <IP> --phase ansible    # Phase 2: configure nodes + NAT
bash scripts/deploy.sh --floating-ip <IP> --phase kubespray  # Phase 3: deploy K8S (~10 min)
bash scripts/deploy.sh --floating-ip <IP> --phase services   # Phase 4: deploy all services
```

### Demo recording mode

```bash
bash scripts/deploy.sh <RESERVATION_ID> --demo
```

Pauses between steps with prompts showing which code files to display. Press Enter to continue.

## What Gets Deployed

### Infrastructure (Phases 1-3)

| Component | Tool | Details |
|-----------|------|---------|
| 3 KVM instances | Terraform | node1 (control-plane + worker + NAT gateway), node2/node3 (workers) |
| Private network | Terraform | 192.168.1.0/24, NAT masquerade via node1 |
| Block storage | Terraform + Ansible | 10Gi (Navidrome) + 5Gi (PostgreSQL), mounted on node1 |
| K8S cluster | Kubespray 2.26 | v1.30, Calico CNI, containerd, metrics-server |
| Docker registry | Ansible (post_k8s) | node1:5000, insecure HTTP, restart=always |
| ArgoCD | Helm (post_k8s) | GitOps deployment manager |
| Argo Workflows | Helm (post_k8s) | CT pipeline engine |

### Services (Phase 4)

| Service | Namespace | NodePort | Description |
|---------|-----------|----------|-------------|
| PostgreSQL | smartqueue-platform | - | User profiles, song catalog, MLflow backend |
| Redis | smartqueue-platform | - | Session state store |
| MLflow | smartqueue-platform | 30500 | Experiment tracking & model registry |
| Navidrome | smartqueue-platform | 30453 | Music streaming with SmartQueue integration |
| Serving (prod) | smartqueue-prod | 30800 | LightGBM ranking (2-8 replicas, HPA @ 60% CPU) |
| Serving (staging) | smartqueue-staging | 30801 | Staging environment |
| Serving (canary) | smartqueue-canary | 30802 | Canary environment |
| Generator | smartqueue-prod | - | Continuous traffic simulator → S3 feedback |
| Prometheus | monitoring | 30090 | Metrics collection (15s scrape) |
| Grafana | monitoring | 30300 | Dashboards (admin / smartqueue) |
| ArgoCD | argocd | 30443 | GitOps UI (admin / auto-generated password) |
| Argo Workflows | argo | 30446 | Pipeline UI (no auth required) |

### Container Images

Built on node1, pushed to `node1:5000`. Managed by `devops/scripts/push-images.sh`:

| Image | Tag | Dockerfile |
|-------|-----|-----------|
| smartqueue-mlflow | v1 | `devops/k8s/platform/mlflow/Dockerfile` |
| smartqueue-serving | v3 | `serving/docker/Dockerfile.lightgbm` |
| smartqueue-navidrome | v2 | `navidrome/Dockerfile` (separate repo) |
| smartqueue-training | v2 | `training/docker/Dockerfile` |
| smartqueue-retrain | v2 | `data/pipelines/pipeline2_retrain/Dockerfile` |
| smartqueue-data | v4 | `data/pipelines/generator/Dockerfile` |

## Deployment Scripts

| Script | Purpose |
|--------|---------|
| `scripts/deploy.sh` | Master orchestrator — runs all 4 phases |
| `scripts/generate-config.sh` | Auto-generates Ansible + Kubespray inventory from terraform output |
| `scripts/push-images.sh` | Builds all 6 images with post-build cache prune |
| `scripts/create-secrets.sh` | Interactive secret creation (fallback) |

### deploy.sh Phases

```
Phase 1: Terraform
  └─ Provision VMs, floating IP, block storage, networks
  └─ Auto-generate inventory files (generate-config.sh)

Phase 2: Ansible
  └─ SCP SSH key to node1
  └─ pre_k8s.yaml: kernel modules, firewall on all nodes
  └─ setup_storage.yaml: format + mount block volumes on node1
  └─ setup_nat.yaml: NAT gateway on node1, worker routes + DNS

Phase 3: Kubespray (runs on node1 via SSH)
  └─ Clone kubespray + SmartQueue + navidrome repos
  └─ Deploy 3-node K8S cluster (~10 min)

Phase 4: Services
  └─ post_k8s.yaml: Docker, registry, Helm, ArgoCD, Argo Workflows
  └─ Create K8S secrets from .secrets file
  └─ Configure containerd insecure registry on all nodes
  └─ Build + push all 6 Docker images
  └─ Deploy: PostgreSQL → Redis → MLflow → Navidrome → Serving → Generator
  └─ Apply CT pipeline CronWorkflow
  └─ Setup daily docker prune cron
```

## Configuration

### Secrets (.secrets file)

```bash
# devops/.secrets (gitignored)
POSTGRES_USER=mlflow
POSTGRES_PASSWORD=smartqueue2026
POSTGRES_DB=mlflow
S3_ACCESS_KEY=<chameleon-ec2-access-key>
S3_SECRET_KEY=<chameleon-ec2-secret-key>
```

### Terraform Variables

```bash
# devops/tf/kvm/terraform.tfvars (gitignored)
cloud_name     = "KVM@TACC"
suffix         = "proj13"
key_pair       = "id_rsa_chameleon"
reservation_id = "<your-lease-reservation-id>"
```

## Post-Deployment

### Access services

```bash
# Get ArgoCD password
ssh -i ~/.ssh/id_rsa_chameleon cc@<FLOATING_IP> \
  "kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"

# Health check
curl http://<FLOATING_IP>:30800/health

# Test recommendation
curl -X POST http://<FLOATING_IP>:30800/queue \
  -H "Content-Type: application/json" \
  -d @shared/sample_input.json
```

### Train initial model

Serving starts with `MOCK_ON_MLFLOW_FAIL=true` (returns random scores). To load a real model:

```bash
ssh -i ~/.ssh/id_rsa_chameleon cc@<FLOATING_IP>
kubectl create -f /home/cc/k8s/training/job-template.yaml
# After training completes, the CT pipeline will update serving with the new model
```

### Monitoring

- **Grafana** (http://FLOATING_IP:30300, admin/smartqueue): 3 dashboards — Cluster, Serving, Fairness
- **Prometheus** (http://FLOATING_IP:30090): Alert rules for CPU, memory, pod health, serving availability

## Troubleshooting

### Disk pressure on node1

node1 root partition is ~37GB. Docker build cache fills it fast.

```bash
ssh cc@<FLOATING_IP> "sudo docker builder prune -af && sudo docker image prune -af"
```

A daily cron job runs automatically to prevent this. push-images.sh also prunes after each build.

### Serving pods not ready

Common causes:
1. No trained model yet → check `MOCK_ON_MLFLOW_FAIL` is `"true"` in deployment
2. MLflow/Postgres down → `kubectl get pods -n smartqueue-platform`
3. Missing secrets → `kubectl get secrets -n smartqueue-prod`

### Workers can't pull images

```bash
# Verify containerd config
ssh cc@<FLOATING_IP> "ssh cc@192.168.1.12 'grep node1 /etc/containerd/config.toml'"
# Verify registry reachable
ssh cc@<FLOATING_IP> "ssh cc@192.168.1.12 'curl -s http://node1:5000/v2/_catalog'"
```

### Kubespray fails at apt update

Workers have no internet. NAT must be configured first:

```bash
bash scripts/deploy.sh --floating-ip <IP> --phase ansible  # includes setup_nat.yaml
```

### Prometheus not scraping

ServiceMonitors must use port **names** (e.g., `http`), not numbers. Check:

```bash
kubectl get servicemonitor -n smartqueue-prod -o yaml | grep port
```
