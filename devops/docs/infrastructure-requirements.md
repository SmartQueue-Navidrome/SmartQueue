# Infrastructure Requirements Table

## Cluster Topology

**Site:** KVM@TACC (Chameleon Cloud)

| Node | Role | Flavor | vCPU | RAM | Disk | Floating IP |
|------|------|--------|------|-----|------|-------------|
| node1 | control plane + worker | m1.large | 10 | 30 GB | 60 GB | Yes (jump host) |
| node2 | worker | m1.large | 10 | 30 GB | 60 GB | No |
| node3 | worker | m1.large | 10 | 30 GB | 60 GB | No |

**Image:** CC-Ubuntu24.04
**Kubernetes:** Kubespray release-2.26, container_manager: containerd, K8S v1.30.6

## Storage

### Block Storage Volumes (Chameleon Cinder)

| Volume | Size | Attached To | Mount Path | Purpose |
|--------|------|-------------|------------|---------|
| vol-navidrome-proj13 | 5 GiB | node1 | /mnt/smartqueue-data/navidrome | Navidrome DB + music |
| vol-postgres-proj13 | 5 GiB | node1 | /mnt/smartqueue-data/postgres | MLflow metadata |

### Object Storage (Chameleon native S3 at CHI@TACC)

| Bucket | Purpose |
|--------|---------|
| `ObjStore_proj13` | MLflow artifacts, datasets, processed features, feedback logs |

Endpoint: `https://chi.tacc.chameleoncloud.org:7480`
Browsable via Chameleon Horizon GUI (Object Store > Containers)

## Service Resource Requests/Limits

> **Note:** Values below are initial estimates. After deployment, actual resource usage will be measured using `kubectl top pods` and `kubectl top nodes` on Chameleon. Evidence screenshots will be appended to this document.

| Service | CPU Request | CPU Limit | Memory Request | Memory Limit | Storage | Replicas | Rationale |
|---------|------------|-----------|----------------|--------------|---------|----------|-----------|
| Navidrome | 200m | 500m | 256Mi | 512Mi | 5Gi PVC | 1 | Go binary + SQLite; lightweight single-user demo |
| PostgreSQL 16 | 250m | 500m | 256Mi | 512Mi | 5Gi PVC | 1 | Only MLflow metadata; low write rate |
| MLflow Tracking | 200m | 500m | 256Mi | 512Mi | — | 1 | Stateless Python server, uses PG + Chameleon S3 backends |
| FastAPI Serving | 250m | 1000m | 256Mi | 512Mi | — | 2–8 (HPA) | ONNX Runtime CPU; ~50MB model; P95 < 150ms target |
| Training Job | 1000m | 2000m | 1Gi | 2Gi | — | 0–1 | LightGBM on 50K–1M rows; bursty, not always running |
| Data Pipeline Job | 500m | 1000m | 512Mi | 1Gi | — | 0–1 | Pandas ETL on parquet files |

**Steady-state total (no training):** ~1.15 CPU, ~1.0Gi memory
**With training burst:** ~2.15 CPU, ~2.0Gi memory
**Cluster capacity:** 30 vCPU, 90GB RAM — ample headroom

## Right-Sizing Evidence

Measured on 2026-04-03 after deploying Navidrome, PostgreSQL, and MLflow (idle state, no training jobs running).

### Node Resource Usage (`kubectl top nodes`)

| Node | CPU (cores) | CPU% | Memory (MiB) | Memory% |
|------|-------------|------|---------------|---------|
| node1 | 285m | 7% | 1289Mi | 17% |
| node2 | 92m | 2% | 975Mi | 12% |
| node3 | 121m | 3% | 540Mi | 7% |

### Pod Resource Usage (`kubectl top pods -n smartqueue-prod`)

| Pod | CPU (cores) | Memory (MiB) |
|-----|-------------|---------------|
| mlflow | 1m | 475Mi |
| navidrome | 1m | 10Mi |
| postgres | 7m | 27Mi |

### Analysis

- **Navidrome** uses minimal resources (1m CPU, 10Mi memory) — well within the 200m/256Mi request. Could reduce memory request to 128Mi.
- **PostgreSQL** is lightweight at idle (7m CPU, 27Mi memory) — well within estimates.
- **MLflow** memory at 475Mi exceeds the 256Mi request but stays under the 512Mi limit. Should increase memory request to 512Mi and limit to 1Gi.
- **Cluster headroom** is significant: only ~12% CPU and ~12% memory used across all 3 nodes, leaving ample room for serving replicas and training jobs.
