# SmartQueue Infrastructure Architecture

## Cluster Overview

3-node Kubernetes (v1.30.6) cluster on Chameleon Cloud KVM@TACC.

| Node | Role | Internal IP | RAM | Disk | Notes |
|------|------|-------------|-----|------|-------|
| node1 | control-plane + worker | 192.168.1.11 | 31Gi | 37G | Floating IP: 129.114.24.226. Docker registry, block storage |
| node2 | worker | 192.168.1.12 | 31Gi | 37G | Internet via NAT through node1 |
| node3 | worker | 192.168.1.13 | 31Gi | 37G | Internet via NAT through node1 |

Only node1 has a public IP. Workers access the internet through NAT masquerade on node1.

## Namespaces

| Namespace | Purpose |
|-----------|---------|
| `smartqueue-platform` | Shared services: PostgreSQL, MLflow, Navidrome, Traefik gateway |
| `smartqueue-prod` | Production serving (2 replicas, HPA up to 8) |
| `smartqueue-canary` | Canary serving (1 replica, receives 10% traffic) |
| `smartqueue-staging` | Staging serving (1 replica, for testing before canary) |
| `argocd` | ArgoCD GitOps controller |
| `argo` | Argo Workflows engine |
| `monitoring` | Prometheus, Grafana, AlertManager |

## Services and Access

### External access (via Floating IP)

| Service | URL | Port |
|---------|-----|------|
| Navidrome | `http://129.114.24.226:30453` | NodePort 30453 |
| MLflow | `http://129.114.24.226:30500` | NodePort 30500 |
| ArgoCD | `http://129.114.24.226:30443` | NodePort 30443 |
| Argo Workflows | `http://129.114.24.226:30446` | NodePort 30446 |
| Serving (prod) | `http://129.114.24.226:30800` | NodePort 30800 |
| Serving (staging) | `http://129.114.24.226:30801` | NodePort 30801 |
| Serving (canary) | `http://129.114.24.226:30802` | NodePort 30802 |
| Traefik Gateway | `http://129.114.24.226:30080` | NodePort 30080 |
| Traefik Dashboard | `http://129.114.24.226:30088` | NodePort 30088 |
| Grafana | `http://129.114.24.226:30300` | NodePort 30300 |
| Prometheus | `http://129.114.24.226:30090` | NodePort 30090 |
| AlertManager | `http://129.114.24.226:30093` | NodePort 30093 |

### Cluster-internal DNS (for inter-service communication)

K8s services are accessible within the cluster using DNS:

```
http://<service-name>.<namespace>.svc.cluster.local:<port>
```

Key internal addresses:

| Service | Internal DNS | Port |
|---------|-------------|------|
| Serving (prod) | `smartqueue-serving.smartqueue-prod.svc.cluster.local` | 8000 |
| Serving (staging) | `smartqueue-serving.smartqueue-staging.svc.cluster.local` | 8000 |
| Serving (canary) | `smartqueue-serving.smartqueue-canary.svc.cluster.local` | 8000 |
| MLflow | `mlflow.smartqueue-platform.svc.cluster.local` | 5000 |
| PostgreSQL | `postgres.smartqueue-platform.svc.cluster.local` | 5432 |
| Navidrome | `navidrome.smartqueue-platform.svc.cluster.local` | 4533 |

These DNS names are derived from the `metadata.name` in each Service manifest. K8s DNS resolves them to pod IPs automatically regardless of which node the pod runs on.

## Serving Environments

Three environments use the same base deployment (`k8s/serving/base/`) with Kustomize overlays:

| Environment | Namespace | Replicas | NodePort | HPA | Purpose |
|-------------|-----------|----------|----------|-----|---------|
| Staging | `smartqueue-staging` | 1 | 30801 | No | Test new model before canary |
| Canary | `smartqueue-canary` | 1 | 30802 | No | Receives 10% production traffic |
| Production | `smartqueue-prod` | 2 (min) | 30800 | Yes (max 8, 60% CPU) | Main serving, 90% traffic |

### Serving container

- Image: `node1:5000/smartqueue-serving:v1`
- Built from: `serving/docker/Dockerfile.lightgbm`
- Port: 8000
- Endpoints: `GET /health`, `POST /queue`, `POST /rank`
- Environment variables:
  - `MLFLOW_TRACKING_URI` — points to cluster-internal MLflow
  - `MODEL_URI` — MLflow run URI (e.g. `runs:/<run-id>/model`)
  - `MODEL_VERSION` — version tag
  - `MLFLOW_S3_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` — S3 credentials for artifact download

### Serving API

Request (`POST /queue`):
```json
{
  "session_id": "abc123",
  "user_features": {
    "skip_rate": 0.3,
    "favorite_genre": "pop",
    "watch_time_avg": 45.2
  },
  "candidate_songs": [
    {
      "video_id": "v001",
      "release_year": 2023,
      "context_segment": "morning",
      "genre": "pop",
      "subgenre": "dance_pop"
    }
  ]
}
```

Response:
```json
{
  "ranked_songs": [
    {
      "video_id": "v001",
      "engagement_probability": 0.82,
      "rank": 1
    }
  ]
}
```

Sample files: `shared/sample_input.json`, `shared/sample_output.json`

## Traffic Routing (Gateway API)

Traefik (v3.1) acts as the Gateway API controller. An HTTPRoute in `smartqueue-platform` routes incoming traffic:

| Path | Backend | Weight |
|------|---------|--------|
| `/api`, `/health`, `/queue`, `/rank` | smartqueue-serving (prod) | 90% |
| `/api`, `/health`, `/queue`, `/rank` | smartqueue-serving (canary) | 10% |
| `/` (catch-all) | navidrome | 100% |

Cross-namespace routing is enabled by ReferenceGrant resources in each serving namespace.

## Docker Images and Private Registry

All custom images are hosted on `node1:5000` (Docker registry, insecure HTTP). Containerd on all nodes is configured to pull from this registry.

| Image | Source Dockerfile | Used By |
|-------|-------------------|---------|
| `node1:5000/smartqueue-serving:v1` | `serving/docker/Dockerfile.lightgbm` | Serving deployments (all envs) |
| `node1:5000/smartqueue-mlflow:v1` | `devops/k8s/platform/mlflow/Dockerfile` | MLflow deployment |
| `node1:5000/smartqueue-training:v1` | `training/docker/Dockerfile` | CT pipeline train step |
| `node1:5000/smartqueue-data:v1` | `data/pipelines/generator/Dockerfile` | CT pipeline generate/retrain steps |

### Building and pushing images

```bash
# SSH to node1
ssh -i ~/.ssh/id_rsa_chameleon cc@129.114.24.226

# Example: rebuild serving image
cd ~/SmartQueue/serving/docker
sudo docker build -f Dockerfile.lightgbm -t node1:5000/smartqueue-serving:v1 ..
sudo docker push node1:5000/smartqueue-serving:v1

# IMPORTANT: clean build cache after building to avoid disk-pressure
sudo docker builder prune -af
sudo docker image prune -af
```

## Storage

### Block storage (Chameleon volumes → hostPath PV)

| Volume | Size | Mount Path (node1) | K8s PV | K8s PVC | Used By |
|--------|------|--------------------|--------|---------|---------|
| vol-navidrome-proj13 | 5 GiB | `/mnt/smartqueue-data/navidrome` | `pv-navidrome` | `navidrome-data` | Navidrome (music DB) |
| vol-postgres-proj13 | 5 GiB | `/mnt/smartqueue-data/postgres` | `pv-postgres` | `postgres-data` | PostgreSQL (MLflow backend) |

Both volumes have node affinity to node1. Pods using these PVCs will always be scheduled on node1.

### Object storage (Chameleon S3)

- Endpoint: `https://chi.tacc.chameleoncloud.org:7480`
- Bucket: `ObjStore_proj13`
- Paths: `raw/`, `processed/`, `feedback/`, `retrain/v{YYYYMMDD}/`
- Credentials stored in K8s Secret `s3-secret` (available in all namespaces)

## CI/CD/CT Pipelines (Argo Workflows)

### CT Pipeline (Continuous Training) — `ct-pipeline`

Runs daily at 2:00 AM UTC. Full automated retraining and deployment:

```
generate-feedback → retrain-data → train-model → evaluate-model
    → deploy-staging → test-staging
    → deploy-canary → canary-monitor (30 min)
    → manual-approval (human gate)
    → deploy-prod
```

| Step | Image | What it does |
|------|-------|-------------|
| generate-feedback | smartqueue-data:v1 | Simulates user traffic, writes feedback to S3 |
| retrain-data | smartqueue-data:v1 | Merges feedback into training dataset on S3 |
| train-model | smartqueue-training:v1 | Trains LightGBM model, logs to MLflow, outputs run-id |
| evaluate-model | python:3.11-slim | Checks val_auc >= 0.65 quality threshold |
| deploy-staging | argocd CLI | Deploys new model to staging via ArgoCD |
| test-staging | curl | Health check + smoke test on staging |
| deploy-canary | argocd CLI | Deploys to canary (receives 10% traffic) |
| canary-monitor | curl | 6 checks over 30 min: health + latency < 2s |
| manual-approval | (suspend) | Human approves promotion to production |
| deploy-prod | argocd CLI | Deploys to production via ArgoCD |

### Deploy-to-Env — `deploy-to-env` (WorkflowTemplate)

Reusable template called by CT pipeline. Two steps:
1. **argocd-sync**: Logs into ArgoCD, sets model URI/version, syncs and waits
2. **audit-log**: Appends deployment record to `model-audit-log` ConfigMap

### Prod Health Rollback — `prod-health-rollback`

CronWorkflow running every 5 minutes:
1. **health-check**: 3 HTTP checks to prod `/health` endpoint. Fails if >= 2 fail.
2. **rollback** (conditional): If unhealthy, runs `kubectl rollout undo`, logs to audit ConfigMap.

### Other templates

| Template | Type | Purpose |
|----------|------|---------|
| `test-staging` | WorkflowTemplate | Health + smoke test for staging |
| `ci-build` | WorkflowTemplate | Build image from git SHA, deploy to staging, test |
| `promote-model` | WorkflowTemplate | Manual model promotion to any environment |

## GitOps (ArgoCD)

Four ArgoCD applications, all tracking `main` branch of `https://github.com/SmartQueue-Navidrome/SmartQueue.git`:

| Application | Repo Path | Sync Policy | Target Namespace |
|-------------|-----------|-------------|------------------|
| smartqueue-platform | `devops/k8s/platform` | Auto (prune + selfHeal) | smartqueue-platform |
| smartqueue-staging | `devops/k8s/serving/overlays/staging` | Manual | smartqueue-staging |
| smartqueue-canary | `devops/k8s/serving/overlays/canary` | Manual | smartqueue-canary |
| smartqueue-prod | `devops/k8s/serving/overlays/production` | Manual | smartqueue-prod |

Platform syncs automatically on git push. Serving environments are synced manually (triggered by Argo Workflows during deployments).

## Secrets

Secrets are created by `devops/scripts/create-secrets.sh` and exist in all namespaces:

| Secret | Keys | Used By |
|--------|------|---------|
| `s3-secret` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | MLflow, training, data, serving |
| `postgres-secret` | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | PostgreSQL, MLflow |
| `argocd-initial-admin-secret` | `password` | deploy-to-env workflow (ArgoCD login) |

No secrets are stored in Git.

## Monitoring

Deployed via kube-prometheus-stack Helm chart into the `monitoring` namespace.

| Component | Access | Purpose |
|-----------|--------|---------|
| Grafana | `http://129.114.24.226:30300` (admin / smartqueue) | Dashboards and visualization |
| Prometheus | `http://129.114.24.226:30090` | Metrics collection and alerting engine |
| AlertManager | `http://129.114.24.226:30093` | Alert routing and notification |
| Node Exporter | (on all 3 nodes) | System-level metrics (CPU, memory, disk, network) |
| kube-state-metrics | (cluster-internal) | K8s object state metrics |

### Custom alert rules (`k8s/monitoring/prometheus-rules.yaml`)

| Alert | Condition | Severity |
|-------|-----------|----------|
| NodeHighCPU | CPU > 85% for 5min | warning |
| NodeHighMemory | Memory > 85% for 5min | warning |
| NodeDiskPressure | Root disk > 80% for 5min | warning |
| PodCrashLooping | > 3 restarts in 10min | critical |
| PVCNearFull | PVC usage > 80% for 5min | warning |
| ServingDown | Serving endpoint unreachable for 1min | critical |
| HPAMaxedOut | HPA at max replicas for 10min | warning |

### Custom Grafana dashboards

- **SmartQueue - Cluster Infrastructure**: Node CPU/memory/disk usage, pod status (running/pending), pod restarts, PVC usage
- **SmartQueue - Serving Performance**: Container CPU/memory per serving pod, HPA replica count, HPA CPU target vs actual, pods per namespace

Plus 27 built-in dashboards from kube-prometheus-stack (K8s resources, networking, node exporter, etc.).

### ServiceMonitor

A ServiceMonitor (`k8s/monitoring/servicemonitor-serving.yaml`) scrapes the serving endpoints across all three environments (prod, staging, canary) every 15s.

## Infrastructure Provisioning

### Terraform (`devops/tf/kvm/`)

Provisions on Chameleon Cloud (OpenStack):
- 3 compute instances (1 with floating IP + sharednet, 2 on private network only)
- Private network 192.168.1.0/24 with fixed IPs
- Security groups: SSH(22), HTTP(80,443), K8s API(6443), NodePort(30000-32767)
- 2 block volumes (5Gi each) attached to node1

### Ansible (`devops/ansible/`)

| Playbook | Purpose |
|----------|---------|
| `pre_k8s.yaml` | Kernel modules, sysctl for K8s networking on all nodes |
| `setup_storage.yaml` | Partition, format, mount block volumes on node1 |
| `post_k8s.yaml` | NAT, Docker registry, Helm, K8s resources, ArgoCD, Argo Workflows |
| `install_monitoring.yaml` | Prometheus + Grafana via kube-prometheus-stack Helm chart |

### Kubespray

K8s cluster deployed using Kubespray release-2.26 from node1:
- Inventory: `kubespray-release/inventory/smartqueue/hosts.yaml`
- Container runtime: containerd 1.7.23
- Network plugin: Calico
- Insecure registry: `node1:5000` configured via containerd mirrors

## SSH Access

```bash
# Connect to node1 (jump host)
ssh -i ~/.ssh/id_rsa_chameleon cc@129.114.24.226

# From node1, connect to workers
ssh -i ~/.ssh/id_rsa_chameleon cc@192.168.1.12  # node2
ssh -i ~/.ssh/id_rsa_chameleon cc@192.168.1.13  # node3

# Or proxy through node1 from your local machine
ssh -o ProxyCommand="ssh -i ~/.ssh/id_rsa_chameleon -W %h:%p cc@129.114.24.226" \
    -i ~/.ssh/id_rsa_chameleon cc@192.168.1.12
```

## Quick Reference

```bash
# kubectl on node1
kubectl get pods -A                           # all pods
kubectl get pods -n smartqueue-prod           # production serving
kubectl top nodes                             # resource usage
kubectl logs -f deploy/smartqueue-serving -n smartqueue-prod  # serving logs

# Check services
curl http://129.114.24.226:30453/ping         # Navidrome health
curl http://129.114.24.226:30500/             # MLflow UI
curl http://129.114.24.226:30800/health       # Serving health (prod)

# ArgoCD
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
# Use this password with username "admin" at http://129.114.24.226:30443

# Docker registry
curl http://129.114.24.226:5000/v2/_catalog   # list images

# Audit log
kubectl get configmap model-audit-log -n smartqueue-prod -o jsonpath='{.data.log}'
```

## Integration Status — What Each Role Needs to Do

### Current state (updated 2026-04-18)

| Component | Status | Notes |
|-----------|--------|-------|
| K8s cluster (3 nodes, 31Gi RAM each) | Running | - |
| Navidrome | Running | Missing SmartQueue env vars + custom image |
| PostgreSQL | Running | - |
| MLflow | Running | 7 training runs, best val_auc=0.8495 |
| Traefik Gateway | Running | - |
| ArgoCD | Running | - |
| Argo Workflows | Running | - |
| Prometheus + Grafana | Running | Grafana(:30300), Prometheus(:30090), AlertManager(:30093) |
| Serving (prod/staging/canary) | CrashLoopBackOff | Waiting for model deployment with real MLflow run ID |
| CT pipeline | Deployed | All 4 images in registry. Ready for end-to-end test once serving is up |
| Prod health rollback | Running | Health checks fail (expected — serving not up yet) |
| Docker images | All built | smartqueue-data, smartqueue-mlflow, smartqueue-serving, smartqueue-training |

### Serving team

1. **Custom Navidrome image**
   - Current deployment uses stock `deluan/navidrome:0.53.3`
   - J3 requires SmartQueue integrated into Navidrome UI
   - Need: image name/tag or Dockerfile for the custom Navidrome build
   - Once provided, DevOps will update `k8s/platform/navidrome/deployment.yaml` with the image and these env vars:
     ```yaml
     env:
       - name: ND_SMARTQUEUE_ENABLED
         value: "true"
       - name: ND_SMARTQUEUE_SERVICEURL
         value: "http://smartqueue-serving.smartqueue-prod.svc.cluster.local:8000"
       - name: ND_SMARTQUEUE_TIMEOUT
         value: "30s"
     ```

2. **`/active-sessions` endpoint**
   - Navidrome UI will poll this every 3s for the live dashboard
   - Confirm this endpoint exists in the serving app and returns the expected format

### Training team

- ~~Train a model~~ Done — 7 runs in MLflow, best run `b5cd1cdfbc3649008ed6bd1355e36004` (val_auc=0.8495)
- ~~Verify training image~~ Done — image works, multiple successful runs
- ~~Confirm quality threshold~~ Done — val_auc=0.85 >> 0.65 threshold

### Data team

- ~~Fix data image~~ Done — was a build context issue, image built and pushed successfully
- **Verify generator works against live serving** — once serving is deployed with real model
- **Data quality checks** — M3 requires data quality evaluation at ingestion/training/production. Great Expectations added to requirements; need integration into pipeline

### Next steps

1. **Deploy model to three environments** — use Training's best run ID to get serving pods running
2. **End-to-end CT pipeline test** — all images ready, trigger a full run
3. **Custom Navidrome image** — waiting on Serving team

### Joint — end-to-end test

Once serving is deployed, trigger a full CT pipeline run:

```bash
# SSH to node1
ssh -i ~/.ssh/id_rsa_chameleon cc@129.114.24.226

# Submit the CT pipeline manually
argo submit -n argo --from cronwf/ct-pipeline --wait
```

Expected flow:
```
generate-feedback (Data)
    → retrain-data (Data)
    → train-model (Training)
    → evaluate-model (quality gate)
    → deploy-staging → test-staging
    → deploy-canary → canary-monitor (30 min)
    → manual-approval (human)
    → deploy-prod
```

### Safeguarding plan (all roles)

Each role owns specific safeguarding principles:

| Principle | Owner | Status |
|-----------|-------|--------|
| Fairness | Data | Not started — monitor engagement rates across genre groups |
| Explainability | Training | Not started — log feature importance per model version in MLflow |
| Transparency | Serving | Not started — `/active-sessions` shows live recommendations |
| Privacy | Data | Not started — no PII in feedback/training data, hash user_id |
| Accountability | DevOps | Done — all deployments/rollbacks logged in `model-audit-log` ConfigMap |
| Robustness | DevOps/Serving | Done — automated rollback via `prod-health-rollback` CronWorkflow |

Need: a written safeguarding plan document covering all six principles with concrete mechanisms.
