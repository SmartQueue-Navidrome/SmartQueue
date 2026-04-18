# SmartQueue Platform Services

**Cluster IP:** `129.114.24.226` (Chameleon KVM@TACC floating IP)

> **Note:** The floating IP changes on each new Chameleon lease. Update the IP below after redeployment.

## Platform Services

| Service | URL | Username | Password | Description |
|---------|-----|----------|----------|-------------|
| Navidrome | `http://129.114.24.226:30453` | admin | *(set on first login)* | Music streaming frontend with SmartQueue recommendation integration |
| MLflow | `http://129.114.24.226:30500` | — | — | Model experiment tracking (parameters, metrics, artifacts) |
| PostgreSQL | cluster-internal only | mlflow | smartqueue2026 | MLflow metadata backend (StatefulSet with PVC) |

## CI/CD

| Service | URL | Username | Password | Description |
|---------|-----|----------|----------|-------------|
| ArgoCD | `https://129.114.24.226:30443` | admin | `qEfWciesuRbCBgp-` | GitOps continuous deployment, syncs K8S resources from git |
| Argo Workflows | `http://129.114.24.226:30446` | — | — | CT pipeline engine, runs daily retrain-train-evaluate-deploy workflow |

## Monitoring

| Service | URL | Username | Password | Description |
|---------|-----|----------|----------|-------------|
| Grafana | `http://129.114.24.226:30300` | admin | `smartqueue` | Visualization dashboards (serving latency, QPS, error rates) |
| Prometheus | `http://129.114.24.226:30090` | — | — | Metrics collection and storage |
| Alertmanager | `http://129.114.24.226:30093` | — | — | Alert routing and notification |

## Serving (Model Inference)

| Service | URL | Description |
|---------|-----|-------------|
| Production | `http://129.114.24.226:30800` | Live serving environment |
| Staging | `http://129.114.24.226:30801` | New model integration testing |
| Canary | `http://129.114.24.226:30802` | Pre-production monitoring (30min health + latency checks) |

## Gateway

| Service | URL | Description |
|---------|-----|-------------|
| Traefik (web) | `http://129.114.24.226:30081` | API gateway, routes requests to serving environments |
| Traefik (dashboard) | `http://129.114.24.226:30088` | Traefik admin dashboard |

## S3 Object Storage (Chameleon CHI@TACC)

| Setting | Value |
|---------|-------|
| Endpoint | `https://chi.tacc.chameleoncloud.org:7480` |
| Bucket | `ObjStore_proj13` |
| Credentials | Stored in K8S secret `s3-secret` (all namespaces) |

## SSH Access

```bash
ssh -i ~/.ssh/id_rsa_chameleon cc@129.114.24.226
```
