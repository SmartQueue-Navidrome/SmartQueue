# DevOps TODO — Milestone 2 & 3

> Last updated: 2026-04-17
> Deadline: M2 demo Apr 20 / freeze Apr 27 / M3 May 4

## DevOps Solo Tasks

- [ ] **Prometheus + Grafana** — Infrastructure monitoring (M3 grading: monitoring + alerting)
  - Dashboards: CPU/memory/disk/pod status
  - Alert rules: disk-pressure, CrashLoopBackOff, high latency, OOM
- [ ] **Infrastructure requirements table** (📝) — CPU/memory requests+limits per service with `kubectl top` evidence
- [ ] **Demo recording** (🎥) — K8s deployment end-to-end + platform services + browser validation
- [ ] **Clean up old resources** — evicted pods, duplicate deployments, unused security groups
- [ ] **README deployment guide** — Steps to reproduce the entire system from scratch
- [ ] **Ongoing operation recording** (🎥, M4) — Periodic videos of system under emulated traffic

## Integration with Teammates

- [ ] **Deploy a real model** (Serving + Training)
  - All serving pods are CrashLoopBackOff — MODEL_URI is a placeholder
  - Training trains a model → Serving confirms it loads → DevOps deploys to all three environments
- [ ] **Fix data image** (Data)
  - `smartqueue-data:v1` build fails: Dockerfile COPY path error (`feedback_checks.py` not found)
  - Data fixes the Dockerfile, then DevOps rebuilds and pushes
- [ ] **Verify training image** (Training)
  - `smartqueue-training:v1` is built — Training needs to confirm `train_ranking_renew.py` runs correctly
- [ ] **Custom Navidrome image** (Serving)
  - J3 requires SmartQueue integrated into Navidrome UI
  - Need the image name/tag or Dockerfile location to update the K8s deployment
- [ ] **End-to-end CT pipeline test** (All)
  - Once all images work, manually trigger a full run:
  - generate-feedback → retrain → train → evaluate → deploy-staging → test → canary → approve → prod
- [ ] **Safeguarding plan document** (All, Training leads)
  - DevOps part done: audit logging (accountability) + automated rollback (robustness)
  - Need a written plan; other roles contribute fairness/explainability/transparency/privacy
- [ ] **Confirm quality threshold** (Training)
  - CT pipeline uses `QUALITY_THRESHOLD=0.65` (val_auc) — is this reasonable?
- [ ] **Data quality checks integration** (Data)
  - M3 requires data quality evaluation at ingestion / training / production
  - Data provides Great Expectations or similar checks, DevOps integrates into pipeline

## Current Cluster Status

- Floating IP: 129.114.24.226
- 3 nodes Ready (v1.30.6), 31Gi RAM each, disk ~50%/21%/25%
- Platform services running: Navidrome(:30453), MLflow(:30500), PostgreSQL, Traefik, ArgoCD(:30443), Argo Workflows(:30446)
- Serving pods: CrashLoopBackOff (waiting for a real model)
- Registry: node1:5000 (smartqueue-mlflow, smartqueue-serving, smartqueue-training)
