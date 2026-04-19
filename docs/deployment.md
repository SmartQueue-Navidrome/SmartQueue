# SmartQueue Deployment Guide

Deploy the full SmartQueue ML-powered music recommendation system from scratch on Chameleon Cloud.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Phase 1: Infrastructure Provisioning (Terraform)](#phase-1-infrastructure-provisioning-terraform)
- [Phase 2: Node Configuration (Ansible)](#phase-2-node-configuration-ansible)
- [Phase 3: Kubernetes Cluster (Kubespray)](#phase-3-kubernetes-cluster-kubespray)
- [Phase 4: Post-K8S Setup](#phase-4-post-k8s-setup)
- [Phase 5: Secrets](#phase-5-secrets)
- [Phase 6: Docker Images](#phase-6-docker-images)
- [Phase 7: Platform Services](#phase-7-platform-services)
- [Phase 8: Monitoring](#phase-8-monitoring)
- [Phase 9: Serving Deployment](#phase-9-serving-deployment)
- [Phase 10: Data Pipeline & Generator](#phase-10-data-pipeline--generator)
- [Phase 11: CI/CD/CT Pipelines](#phase-11-cicdct-pipelines)
- [Phase 12: Verification](#phase-12-verification)
- [Troubleshooting](#troubleshooting)
- [Architecture Reference](#architecture-reference)

---

## Prerequisites

- Chameleon Cloud account with an active lease on KVM@TACC
- SSH key pair registered on Chameleon (`~/.ssh/id_rsa_chameleon`)
- Local tools: Terraform, Ansible, kubectl, Helm, Docker CLI
- Chameleon S3 credentials (EC2-style from CHI@TACC Application Credentials)

## Phase 1: Infrastructure Provisioning (Terraform)

Terraform creates 3 KVM instances, a private network, security groups, and 2 block storage volumes.

### Cluster topology

| Node  | Role                    | Flavor   | Private IP     |
|-------|-------------------------|----------|----------------|
| node1 | control-plane + worker  | m1.xxlarge | 192.168.1.11   |
| node2 | worker                  | m1.xxlarge | 192.168.1.12   |
| node3 | worker                  | m1.xxlarge | 192.168.1.13   |

node1 gets a floating IP for external SSH access. Workers reach the internet via NAT through node1.

### Steps

```bash
cd devops/tf/kvm/

# Create terraform.tfvars from your Chameleon lease
cat > terraform.tfvars <<'EOF'
cloud_name      = "KVM@TACC"
suffix          = "proj13"
key_pair        = "your-keypair-name"
reservation_id  = "your-chameleon-reservation-id"
image           = "CC-Ubuntu24.04"
flavor          = "m1.large"
node_count      = 3
private_subnet_cidr = "192.168.1.0/24"
EOF

terraform init
terraform plan
terraform apply

# Note outputs
terraform output floating_ip        # e.g. 129.114.24.226
terraform output volume_devices     # e.g. {navidrome: /dev/vdc, postgres: /dev/vdb}
```

### What gets created

- **Network:** `smartqueue-net` (192.168.1.0/24), router to `sharednet1`
- **Security groups:** SSH (22), HTTP (80/443/4533), K8S API (6443), NodePorts (30000-32767)
- **Volumes:** 2x 5Gi Cinder volumes attached to node1 (for PostgreSQL and Navidrome)

---

## Phase 2: Node Configuration (Ansible)

### Update inventory

Edit `devops/ansible/inventory/hosts.ini` with your floating IP:

```ini
[all]
node1 ansible_host=<FLOATING_IP>
node2 ansible_host=192.168.1.12 ansible_ssh_common_args='-o ProxyCommand="ssh -o StrictHostKeyChecking=no -i ~/.ssh/id_rsa_chameleon -W %h:%p cc@<FLOATING_IP>"'
node3 ansible_host=192.168.1.13 ansible_ssh_common_args='-o ProxyCommand="ssh -o StrictHostKeyChecking=no -i ~/.ssh/id_rsa_chameleon -W %h:%p cc@<FLOATING_IP>"'

[all:vars]
ansible_user=cc
ansible_ssh_private_key_file=~/.ssh/id_rsa_chameleon

[control_plane]
node1

[workers]
node1
node2
node3

[storage_nodes]
node1
```

### Run playbooks

```bash
cd devops/ansible/

# 1. Kernel modules and sysctl for K8S networking (all nodes)
ansible-playbook -i inventory/hosts.ini playbooks/pre_k8s.yaml

# 2. Format and mount block volumes on node1
#    Edit playbooks/setup_storage.yaml device paths to match terraform output
ansible-playbook -i inventory/hosts.ini playbooks/setup_storage.yaml

# Verify mounts
ssh -i ~/.ssh/id_rsa_chameleon cc@<FLOATING_IP> "mount | grep smartqueue-data"
# Expected:
#   /dev/vdb on /mnt/smartqueue-data/postgres type ext4
#   /dev/vdc on /mnt/smartqueue-data/navidrome type ext4
```

### What this configures

- `br_netfilter` kernel module loaded on all nodes
- `net.ipv4.ip_forward=1` on all nodes
- Block volumes formatted as ext4 and mounted at `/mnt/smartqueue-data/{postgres,navidrome}`

---

## Phase 3: Kubernetes Cluster (Kubespray)

### Update Kubespray inventory

Edit `devops/kubespray/inventory/smartqueue/hosts.yaml` with your IPs and SSH key path. Key settings in `group_vars/k8s_cluster.yml`:

```yaml
container_manager: containerd
metrics_server_enabled: true
kube_network_plugin: calico

# Private insecure registry
containerd_insecure_registries:
  "node1:5000": "http://192.168.1.11:5000"
```

### Deploy cluster

```bash
cd devops/kubespray-release   # Kubespray release-2.26 submodule

ansible-playbook -i ../kubespray/inventory/smartqueue/hosts.yaml \
  --become \
  cluster.yml
# Takes ~30 minutes
```

### Verify

```bash
ssh -i ~/.ssh/id_rsa_chameleon cc@<FLOATING_IP>
kubectl get nodes
# NAME    STATUS   ROLES           AGE   VERSION
# node1   Ready    control-plane   5m    v1.30.6
# node2   Ready    <none>          4m    v1.30.6
# node3   Ready    <none>          4m    v1.30.6
```

---

## Phase 4: Post-K8S Setup

This playbook sets up NAT routing, Docker registry, Helm, namespaces, ArgoCD, and Argo Workflows in one run.

```bash
cd devops/ansible/
ansible-playbook -i inventory/hosts.ini playbooks/post_k8s.yaml
```

### What this creates

| Component | Details |
|-----------|---------|
| **NAT** | node1 masquerades for 192.168.1.0/24 so workers can reach the internet |
| **Docker registry** | `registry:2` on node1:5000 (HTTP, insecure) |
| **Helm** | Installed on node1 |
| **Namespaces** | smartqueue-platform, smartqueue-prod, smartqueue-staging, smartqueue-canary, argocd, argo, monitoring |
| **PersistentVolumes** | pv-postgres (5Gi), pv-navidrome (5Gi) — hostPath backed by Cinder volumes |
| **Gateway API CRDs** | v1.1.0 |
| **ArgoCD** | Helm chart, NodePort 30443, `--insecure` mode |
| **ArgoCD Apps** | platform, staging, canary, production — tracking `main` branch |
| **Argo Workflows** | Helm chart, NodePort 30446, ServiceAccount + RBAC for workflow execution |
| **WorkflowTemplates** | CT pipeline, CI build, deploy-to-env, test-staging, rollback, promote |

### Verify

```bash
ssh -i ~/.ssh/id_rsa_chameleon cc@<FLOATING_IP>

# ArgoCD
kubectl get pods -n argocd          # All Running
# Get admin password:
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
# Access UI: http://<FLOATING_IP>:30443

# Argo Workflows
kubectl get pods -n argo            # All Running
# Access UI: http://<FLOATING_IP>:30446
```

---

## Phase 5: Secrets

Create secrets across all namespaces. You will need:
- PostgreSQL password (choose one)
- Chameleon S3 credentials (from CHI@TACC > Identity > Application Credentials > EC2)

```bash
ssh -i ~/.ssh/id_rsa_chameleon cc@<FLOATING_IP>

# Interactive — prompts for PostgreSQL password and S3 keys
bash ~/SmartQueue/devops/scripts/create-secrets.sh
```

This creates `postgres-secret` and `s3-secret` in all 4 namespaces (platform, prod, staging, canary).

Also copy the s3-secret to the `argo` namespace for CT pipeline workflows:

```bash
kubectl get secret s3-secret -n smartqueue-platform -o yaml \
  | sed 's/namespace: smartqueue-platform/namespace: argo/' \
  | kubectl apply -f -
```

And copy the ArgoCD admin secret to `argo` namespace for the deploy workflow:

```bash
kubectl get secret argocd-initial-admin-secret -n argocd -o yaml \
  | sed 's/namespace: argocd/namespace: argo/' \
  | kubectl apply -f -
```

---

## Phase 6: Docker Images

Build and push all container images from node1 (which has both Docker and the registry).

```bash
ssh -i ~/.ssh/id_rsa_chameleon cc@<FLOATING_IP>
cd ~/SmartQueue

# 1. MLflow server
docker build -t node1:5000/smartqueue-mlflow:v1 \
  -f devops/k8s/platform/mlflow/Dockerfile \
  devops/k8s/platform/mlflow/
docker push node1:5000/smartqueue-mlflow:v1

# 2. Serving (LightGBM FastAPI)
docker build -t node1:5000/smartqueue-serving:v3 \
  -f serving/lightgbm_app/Dockerfile \
  serving/lightgbm_app/
docker push node1:5000/smartqueue-serving:v3

# 3. Training
docker build -t node1:5000/smartqueue-training:v1 \
  -f training/docker/Dockerfile \
  training/
docker push node1:5000/smartqueue-training:v1

# 4. Data generator
docker build -t node1:5000/smartqueue-data:v3 \
  -f data/pipelines/generator/Dockerfile \
  data/pipelines/generator/
docker push node1:5000/smartqueue-data:v3

# 5. Navidrome (optional custom build, or use upstream)
docker build -t node1:5000/smartqueue-navidrome:v1 \
  -f devops/k8s/platform/navidrome/Dockerfile \
  devops/k8s/platform/navidrome/ 2>/dev/null \
  || docker pull deluan/navidrome:latest && \
     docker tag deluan/navidrome:latest node1:5000/smartqueue-navidrome:v1
docker push node1:5000/smartqueue-navidrome:v1

# Clean build cache to avoid disk pressure
docker builder prune -af

# Verify all images in registry
curl -s http://node1:5000/v2/_catalog | python3 -m json.tool
```

**Important:** Always run `docker builder prune -af` after building to prevent disk pressure on node1 (only 60GB disk).

---

## Phase 7: Platform Services

Deploy PostgreSQL, MLflow, and Navidrome.

```bash
ssh -i ~/.ssh/id_rsa_chameleon cc@<FLOATING_IP>

# PostgreSQL
kubectl apply -f ~/k8s/platform/postgres/

# Wait for PostgreSQL to be ready
kubectl rollout status statefulset postgres -n smartqueue-platform --timeout=120s

# MLflow
kubectl apply -f ~/k8s/platform/mlflow/

# Redis (shared session store for serving)
kubectl apply -f ~/k8s/platform/redis/
kubectl rollout status deployment/redis -n smartqueue-platform --timeout=60s

# Navidrome
kubectl apply -f ~/k8s/platform/navidrome/

# Gateway (Traefik + HTTPRoute)
kubectl apply -f ~/k8s/platform/gateway/
```

### Verify

```bash
# PostgreSQL
kubectl exec -it postgres-0 -n smartqueue-platform -- psql -U mlflow -d mlflow -c "\l"

# MLflow UI
curl -s http://localhost:30500/health    # or from local: http://<FLOATING_IP>:30500

# Redis
kubectl exec -n smartqueue-platform deployment/redis -- redis-cli ping
# Expected: PONG

# Navidrome
curl -s http://localhost:30453/ping      # or from local: http://<FLOATING_IP>:30453
```

### Service ports

| Service    | Namespace            | ClusterIP Port | NodePort |
|------------|----------------------|----------------|----------|
| PostgreSQL | smartqueue-platform  | 5432           | -        |
| MLflow     | smartqueue-platform  | 5000           | 30500    |
| Redis      | smartqueue-platform  | 6379           | -        |
| Navidrome  | smartqueue-platform  | 4533           | 30453    |

---

## Phase 8: Monitoring

Install Prometheus + Grafana + AlertManager via kube-prometheus-stack.

```bash
cd devops/ansible/
ansible-playbook -i inventory/hosts.ini playbooks/install_monitoring.yaml
```

This installs kube-prometheus-stack with:
- Grafana on NodePort **30300** (admin / smartqueue)
- Prometheus on NodePort **30090**
- AlertManager on NodePort **30093**

### Deploy ServiceMonitors and dashboards

```bash
ssh -i ~/.ssh/id_rsa_chameleon cc@<FLOATING_IP>

# ServiceMonitor for serving pods (prod, staging, canary)
kubectl apply -f ~/k8s/monitoring/servicemonitor-serving.yaml

# ServiceMonitor + Service for generator (fairness metrics)
kubectl apply -f ~/k8s/monitoring/servicemonitor-generator.yaml

# Custom alert rules
kubectl apply -f ~/k8s/monitoring/prometheus-rules.yaml

# Grafana dashboards
for dashboard in cluster serving fairness; do
  kubectl create configmap grafana-dashboard-${dashboard} \
    --from-file=${dashboard}.json=/home/cc/k8s/monitoring/grafana-dashboards/${dashboard}.json \
    --namespace monitoring \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl label configmap grafana-dashboard-${dashboard} \
    grafana_dashboard=1 \
    --namespace monitoring --overwrite
done
```

### Dashboards

| Dashboard | UID | Description |
|-----------|-----|-------------|
| SmartQueue - Cluster Infrastructure | smartqueue-cluster | Node CPU/memory, pod status, disk usage |
| SmartQueue - Serving Performance | smartqueue-serving | Request rate, latency, error rate, predictions, active sessions |
| SmartQueue - Fairness Monitoring | smartqueue-fairness | Per-genre engagement rate, distribution, drift detection |

---

## Phase 9: Serving Deployment

Serving is deployed to 3 environments via Kustomize overlays, managed by ArgoCD.

### Deploy via ArgoCD sync

```bash
ssh -i ~/.ssh/id_rsa_chameleon cc@<FLOATING_IP>

ARGOCD_PASS=$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)
argocd login argocd-server.argocd.svc.cluster.local --username admin --password "$ARGOCD_PASS" --plaintext

# Sync all three environments
argocd app sync smartqueue-staging
argocd app sync smartqueue-canary
argocd app sync smartqueue-prod
```

### Or deploy manually with kubectl

```bash
# Staging (1 replica, NodePort 30801)
kubectl apply -k ~/k8s/serving/overlays/staging/

# Canary (1 replica, NodePort 30802)
kubectl apply -k ~/k8s/serving/overlays/canary/

# Production (2 replicas + HPA, NodePort 30800)
kubectl apply -k ~/k8s/serving/overlays/production/
```

### Verify

```bash
# Check all serving pods
kubectl get pods -n smartqueue-prod -l app=smartqueue-serving
kubectl get pods -n smartqueue-staging -l app=smartqueue-serving
kubectl get pods -n smartqueue-canary -l app=smartqueue-serving

# Health check
curl http://<FLOATING_IP>:30800/health

# Smoke test
curl -X POST http://<FLOATING_IP>:30800/queue \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-001",
    "user_features": {
      "user_skip_rate": 0.3,
      "user_favorite_genre_encoded": 5,
      "user_watch_time_avg": 45.2
    },
    "candidate_songs": [{
      "video_id": "v001",
      "release_year": 2023,
      "context_segment": 0,
      "genre_encoded": 5,
      "subgenre_encoded": 10
    }]
  }'
```

### Environment overview

| Environment | Namespace          | Replicas | HPA | NodePort |
|-------------|--------------------|----------|-----|----------|
| Production  | smartqueue-prod    | 2 (min 2, max 8) | CPU 60% | 30800 |
| Staging     | smartqueue-staging | 1        | No  | 30801    |
| Canary      | smartqueue-canary  | 1        | No  | 30802    |

---

## Phase 10: Data Pipeline & Generator

### Initial data ingestion (Pipeline 1)

Run once to ingest the XITE dataset and create train/val/test/production splits:

```bash
ssh -i ~/.ssh/id_rsa_chameleon cc@<FLOATING_IP>
cd ~/SmartQueue/data

# Option A: Run locally
bash pipelines/pipeline1_initial/run_pipeline.sh --output-dir /tmp/smartqueue

# Option B: Run via Docker
docker compose up pipeline1
```

This produces `processed/production.parquet` which the generator needs.

### Deploy generator to K8S

The generator runs continuously in `smartqueue-prod`, replaying production sessions against the serving endpoint and uploading feedback to S3.

```bash
kubectl apply -f ~/k8s/data/generator-deployment.yaml

# Verify
kubectl get pods -n smartqueue-prod -l app=smartqueue-generator
kubectl logs -f deploy/smartqueue-generator -n smartqueue-prod
```

### Generator configuration

| Variable | Default | Description |
|----------|---------|-------------|
| QUEUE_ENDPOINT | (required) | Serving /queue URL |
| CONCURRENCY | 10 | Concurrent sessions |
| CANDIDATES_PER_REQ | 10 | Songs per ranking request |
| FEEDBACK_DELAY | 3.0 | Seconds between song feedback |
| LOCAL_MODE | false | Skip S3 upload when true |

### S3 bucket structure

```
ObjStore_proj13/
├── raw/xite_msd.parquet              # Source dataset
├── processed/                         # Feature-engineered splits
│   ├── train.parquet
│   ├── val.parquet
│   ├── test.parquet
│   └── production.parquet
├── feedback/                          # Generator output
│   └── {YYYYMMDD}/                   # One folder per date
│       └── {YYYYMMDD}_{session_id}_{loop}_{run}.jsonl
├── retrain/                           # Daily retrain datasets
│   └── v{YYYYMMDD}/train.parquet
└── mlflow-artifacts/                  # MLflow model artifacts
```

---

## Phase 11: CI/CD/CT Pipelines

All pipelines run as Argo Workflows in the `argo` namespace.

### Continuous Training (CT) pipeline

Runs daily at 02:00 UTC via CronWorkflow:

```
retrain-data → train-model → evaluate-model (AUC >= 0.65)
  → deploy-staging → test-staging
  → deploy-canary → canary-monitor (30 min)
  → manual-approval → deploy-prod
```

```bash
# Verify CronWorkflow is registered
kubectl get cronwf -n argo

# Trigger manually
argo submit -n argo --from cronwf/ct-pipeline

# Watch progress
argo watch -n argo @latest
```

### Deploy-to-env WorkflowTemplate

Used by CT pipeline to deploy to any environment. Uses ArgoCD CLI to:
1. Set the model URI and version on the serving deployment
2. Sync and wait for rollout
3. Append an entry to the `model-audit-log` ConfigMap

### Production health rollback

CronWorkflow running every 5 minutes. Checks `/health` on the production serving endpoint and triggers `kubectl rollout undo` if 2+ out of 3 checks fail.

### CI build pipeline

Triggered on code changes. Builds Docker images, pushes to node1:5000, and deploys to staging for validation.

---

## Phase 12: Verification

### Full system health check

```bash
ssh -i ~/.ssh/id_rsa_chameleon cc@<FLOATING_IP>

echo "=== Nodes ==="
kubectl get nodes

echo "=== Platform ==="
kubectl get pods -n smartqueue-platform

echo "=== Serving ==="
kubectl get pods -n smartqueue-prod -l app=smartqueue-serving
kubectl get pods -n smartqueue-staging -l app=smartqueue-serving
kubectl get pods -n smartqueue-canary -l app=smartqueue-serving

echo "=== Generator ==="
kubectl get pods -n smartqueue-prod -l app=smartqueue-generator

echo "=== HPA ==="
kubectl get hpa -n smartqueue-prod

echo "=== ArgoCD ==="
kubectl get pods -n argocd

echo "=== Argo Workflows ==="
kubectl get cronwf -n argo

echo "=== Monitoring ==="
kubectl get pods -n monitoring | grep -E 'grafana|prometheus|alertmanager'
```

### Endpoint tests

```bash
# MLflow
curl -s http://<FLOATING_IP>:30500/health

# Navidrome
curl -s http://<FLOATING_IP>:30453/ping

# Serving (prod)
curl -s http://<FLOATING_IP>:30800/health

# Grafana
curl -s http://<FLOATING_IP>:30300/api/health

# Prometheus targets
curl -s http://<FLOATING_IP>:30090/api/v1/targets | python3 -c "
import sys, json
targets = json.load(sys.stdin)['data']['activeTargets']
for t in targets:
    if 'smartqueue' in t.get('labels',{}).get('job',''):
        print(f\"{t['labels']['job']:40s} {t['health']}\")
"
```

---

## Troubleshooting

### Disk pressure on node1

node1 only has ~60GB disk. Docker images and registry layers accumulate quickly.

**Immediate fix:**
```bash
docker builder prune -af
docker image prune -f           # dangling only, NOT -af
docker exec registry /bin/registry garbage-collect \
  /etc/docker/registry/config.yml --delete-untagged
```

**Long-term:** A `disk-cleanup` CronJob runs daily at 01:00 UTC (`devops/k8s/platform/disk-cleanup-cronjob.yaml`). Deploy it:
```bash
kubectl apply -f ~/k8s/platform/disk-cleanup-cronjob.yaml
```

### Serving pods CrashLoopBackOff

Common causes:
1. **MLflow down** (postgres evicted by disk pressure) — fix disk pressure first, then restart postgres
2. **Missing S3 credentials** — verify `s3-secret` exists in the serving namespace
3. **Model download timeout** — startupProbe allows up to 300s (30 x 10s), check MLflow connectivity

```bash
kubectl logs -f deploy/smartqueue-serving -n smartqueue-prod
kubectl describe pod -n smartqueue-prod -l app=smartqueue-serving
```

### Worker nodes can't pull images

Workers access node1:5000 via the private network. If pulls fail:
1. Check NAT is working: `ssh node2 "curl -s http://node1:5000/v2/_catalog"`
2. Check containerd config includes insecure registry: `ssh node2 "cat /etc/containerd/config.toml | grep -A2 node1"`
3. Clear stale cached images: `ssh node2 "sudo crictl rmi node1:5000/smartqueue-serving:v1"` then restart the pod

### ArgoCD login fails in Argo Workflows

The deploy-to-env template uses `argocd login ... --plaintext` (no TLS). If login fails:
- Ensure the `argocd-initial-admin-secret` exists in the `argo` namespace
- Verify the ArgoCD server is reachable: `kubectl exec -n argo <pod> -- curl -s argocd-server.argocd.svc.cluster.local`

### Prometheus not scraping SmartQueue metrics

Check that ServiceMonitors use port **names** (not numbers):
```bash
# Correct: port name matching the Service port name
kubectl get svc smartqueue-serving -n smartqueue-prod -o jsonpath='{.spec.ports[0].name}'
# Should match the ServiceMonitor's port field
```

---

## Architecture Reference

### Network ports

| Port  | Service | Protocol |
|-------|---------|----------|
| 22    | SSH (node1 only) | TCP |
| 6443  | K8S API | TCP |
| 5000  | Docker Registry (node1 internal) | HTTP |
| 30080 | Traefik Gateway | HTTP |
| 30300 | Grafana | HTTP |
| 30090 | Prometheus | HTTP |
| 30093 | AlertManager | HTTP |
| 30443 | ArgoCD | HTTP |
| 30446 | Argo Workflows UI | HTTP |
| 30453 | Navidrome | HTTP |
| 30500 | MLflow | HTTP |
| 30800 | Serving (prod) | HTTP |
| 30801 | Serving (staging) | HTTP |
| 30802 | Serving (canary) | HTTP |

### Container images

| Image | Dockerfile | Build context |
|-------|-----------|---------------|
| smartqueue-mlflow:v1 | `devops/k8s/platform/mlflow/Dockerfile` | `devops/k8s/platform/mlflow/` |
| smartqueue-serving:v3 | `serving/lightgbm_app/Dockerfile` | `serving/lightgbm_app/` |
| smartqueue-training:v1 | `training/docker/Dockerfile` | `training/` |
| smartqueue-data:v3 | `data/pipelines/generator/Dockerfile` | `data/pipelines/generator/` |
| smartqueue-navidrome:v1 | custom or `deluan/navidrome` | - |

### Key internal DNS

| Service | Address |
|---------|---------|
| PostgreSQL | `postgres.smartqueue-platform.svc.cluster.local:5432` |
| MLflow | `mlflow.smartqueue-platform.svc.cluster.local:5000` |
| Serving (prod) | `smartqueue-serving.smartqueue-prod.svc.cluster.local:8000` |
| Serving (staging) | `smartqueue-serving.smartqueue-staging.svc.cluster.local:8000` |
| Serving (canary) | `smartqueue-serving.smartqueue-canary.svc.cluster.local:8000` |
| Redis | `redis.smartqueue-platform.svc.cluster.local:6379` |
| ArgoCD | `argocd-server.argocd.svc.cluster.local` |
| S3 (Chameleon) | `https://chi.tacc.chameleoncloud.org:7480` |
