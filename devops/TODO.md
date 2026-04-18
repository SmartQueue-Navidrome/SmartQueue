# DevOps TODO — Milestone 2 & 3

> Last updated: 2026-04-18
> Deadline: M2 demo Apr 20 / freeze Apr 27 / M3 May 4

## DevOps Solo Tasks

- [x] **Prometheus + Grafana** — Infrastructure monitoring (M3 grading: monitoring + alerting)
  - Dashboards: CPU/memory/disk/pod status + serving performance
  - Alert rules: disk-pressure, CrashLoopBackOff, high latency, OOM, ServingDown, HPAMaxedOut, PVCNearFull
  - Access: Grafana(:30300) admin/smartqueue, Prometheus(:30090), AlertManager(:30093)
- [ ] **Infrastructure requirements table** (📝) — CPU/memory requests+limits per service with `kubectl top` evidence
- [ ] **Demo recording** (🎥) — K8s deployment end-to-end + platform services + browser validation
- [ ] **Clean up old resources** — evicted pods, duplicate deployments, unused security groups
- [ ] **README deployment guide** — Steps to reproduce the entire system from scratch
- [ ] **Ongoing operation recording** (🎥, M4) — Periodic videos of system under emulated traffic

## Integration with Teammates

- [ ] **Deploy a real model to three environments** (DevOps)
  - Training has a model: run `b5cd1cdf...` with val_auc=0.8495 (FINISHED)
  - Need to deploy to staging → canary → prod with this run ID
- [x] ~~**Fix data image** (Data)~~ — was a build context issue, not a Dockerfile bug. Fixed and pushed.
- [x] ~~**Verify training image** (Training)~~ — Training ran 7 runs on MLflow, image works. val_auc=0.85.
- [ ] **Custom Navidrome image** (Serving)
  - J3 requires SmartQueue integrated into Navidrome UI
  - Need the image name/tag or Dockerfile location to update the K8s deployment
- [ ] **End-to-end CT pipeline test** (All)
  - All 4 images now in registry. Once serving is deployed, manually trigger a full CT run.
- [ ] **Safeguarding plan document** (All, Training leads)
  - DevOps part done: audit logging (accountability) + automated rollback (robustness)
  - Need a written plan; other roles contribute fairness/explainability/transparency/privacy
- [x] ~~**Confirm quality threshold** (Training)~~ — val_auc=0.85 >> 0.65 threshold. Confirmed OK.
- [ ] **Data quality checks integration** (Data)
  - M3 requires data quality evaluation at ingestion / training / production
  - Data added great-expectations to requirements; need integration into pipeline

## Current Cluster Status

- Floating IP: 129.114.24.226
- 3 nodes Ready (v1.30.6), 31Gi RAM each, disk ~50%/21%/25%
- Platform services running: Navidrome(:30453), MLflow(:30500), PostgreSQL, Traefik, ArgoCD(:30443), Argo Workflows(:30446)
- Monitoring running: Grafana(:30300), Prometheus(:30090), AlertManager(:30093)
- Serving pods: CrashLoopBackOff (waiting for model deployment)
- Registry: node1:5000 (smartqueue-data, smartqueue-mlflow, smartqueue-serving, smartqueue-training)
- MLflow: 7 runs in `smartqueue-stage-b`, best val_auc=0.8495 (run b5cd1cdf...)
