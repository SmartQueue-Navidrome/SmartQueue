# SmartQueue

A machine learning-powered music recommendation engine integrated into [Navidrome](https://www.navidrome.org/). SmartQueue re-ranks a user's song queue in real time using a personalized LightGBM model trained on listening behavior, deployed on Kubernetes with a fully automated continuous training pipeline.

---

## System Overview

```
User listens on Navidrome
        │
        ▼
Serving (FastAPI) — LightGBM ranks songs by predicted engagement probability
        │
        ▼
Feedback captured → S3 object storage
        │
        ▼
Daily CT pipeline (Argo Workflows) — retrain → quality gate → MLflow Registry → deploy
```

---

## Repository Structure

```
SmartQueue/
├── training/               # Model training pipeline
│   ├── train_ranking_processed.py   # LightGBM training + quality gate + MLflow logging
│   ├── configs/
│   │   └── stage_b_lgbm_v4.yaml    # Training config (2M samples, 300 rounds)
│   └── docker/Dockerfile           # Training container image
│
├── serving/                # Model serving
│   ├── lightgbm_app/app.py         # FastAPI app — /queue, /session/end, /health, /metrics
│   └── monitoring/promotion_triggers.py  # Automated rollback on metric degradation
│
├── data/                   # Data pipelines
│   ├── pipelines/pipeline1_initial/      # Initial feature engineering
│   ├── pipelines/pipeline2_retrain/      # Feedback → retrain dataset
│   └── pipelines/generator/generator.py  # Production traffic simulator
│
├── devops/                 # Infrastructure & automation
│   ├── k8s/                          # Kubernetes manifests (platform, serving, monitoring)
│   └── workflows/
│       ├── ct-pipeline.yaml          # Daily CT pipeline (Argo CronWorkflow)
│       └── prod-health-rollback.yaml # Automated rollback workflow
│
└── docs/
    ├── deployment.md       # Full deployment guide
    └── safeguarding.md     # Fairness, explainability, privacy, accountability, robustness
```

---

## ML Feature

SmartQueue integrates a personalized ranking model directly into Navidrome's queue system. When a user plays music, the serving API re-orders candidate songs using a LightGBM binary classifier that predicts engagement (skip vs. listen) based on:

| Feature | Description |
|---------|-------------|
| `user_skip_rate` | User's historical skip rate |
| `user_watch_time_avg` | User's average listening duration |
| `user_favorite_genre_encoded` | User's most-listened genre |
| `release_year` | Song release year |
| `genre_encoded` | Song genre |
| `subgenre_encoded` | Song subgenre |
| `context_segment` | Time-of-day listening context |

---

## Infrastructure

3-node Kubernetes cluster on [Chameleon Cloud](https://chameleoncloud.org/) (KVM@TACC).

| Service | URL |
|---------|-----|
| Navidrome | http://129.114.24.226:30453 |
| MLflow | http://129.114.24.226:30500 |
| Grafana | http://129.114.24.226:30300 |
| Argo Workflows | http://129.114.24.226:30446 |
| ArgoCD | http://129.114.24.226:30443 |
| Serving (prod) | http://129.114.24.226:30800 |

**Namespaces:** `smartqueue-platform` (shared services) · `smartqueue-prod` · `smartqueue-staging` · `smartqueue-canary` · `monitoring` · `argo` · `argocd`

---

## Continuous Training Pipeline

The CT pipeline runs daily at 2 AM UTC via Argo CronWorkflow (`devops/workflows/ct-pipeline.yaml`):

1. **retrain-data** — Download latest feedback data from S3
2. **train-model** — Train LightGBM on retrain dataset, log to MLflow
3. **evaluate-model** — Verify AUC meets minimum threshold
4. **deploy-staging** → **test-staging** — Deploy and smoke-test in staging
5. **deploy-canary** → **canary-monitor** — 30-minute canary health + latency check
6. **manual-approval** — Human sign-off before production
7. **deploy-prod** — Promote to production

### Quality Gate

Before a model is registered to MLflow, it must pass three checks (in `training/train_ranking_processed.py`):

| Rule | Threshold | Rationale |
|------|-----------|-----------|
| `val_auc >= 0.75` | Absolute floor | 50% of the way from random (0.5) to perfect (1.0) |
| `val_logloss <= 0.65` | Absolute ceiling | Ensures well-calibrated probability outputs |
| `val_auc > prod_auc + 0.002` | Relative improvement | Prevents noise-level changes from deploying |

Failed runs are logged to MLflow but not registered. Passing runs are registered to `smartqueue-ranking` at Staging; the serving team promotes to Production.

---

## Safeguarding

See [docs/safeguarding.md](docs/safeguarding.md) for the full plan covering:

- **Fairness** — Per-genre engagement rate monitored in Grafana; drift detection on retrain data
- **Explainability** — Gain-based feature importance logged to MLflow for every training run
- **Transparency** — Live session dashboard in Navidrome UI; `/health` exposes active model version
- **Privacy** — No PII in dataset or feedback logs (opaque session IDs only)
- **Accountability** — Full audit trail via MLflow run IDs + Argo Workflows execution history
- **Robustness** — Canary deployment, latency gate, automated rollback on metric degradation

---

## Deployment

See [docs/deployment.md](docs/deployment.md) for full instructions to reproduce the system from scratch.
