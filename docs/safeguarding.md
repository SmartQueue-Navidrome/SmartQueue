# SmartQueue — Safeguarding Plan

> **Owner:** All roles (Training leads document; each role owns their section)
> **Last updated:** 2026-04-18

---

## Overview

SmartQueue is a music queue ranking system that personalises song recommendations for Navidrome users using a LightGBM model trained on listening behaviour. This document describes the concrete mechanisms implemented to ensure the system is fair, explainable, transparent, private, accountable, and robust.

---

## 1. Fairness

**Owner: Data**

**Concern:** The ranking model may systematically favour certain music genres over others, reducing diversity and creating feedback loops where underrepresented genres receive even less exposure over time.

**Mechanism implemented:**

The data generator (`data/pipelines/generator/generator.py`) exposes two Prometheus counters scraped every 15 seconds via a dedicated `/metrics` endpoint (port 8001):

| Metric | Description |
|--------|-------------|
| `genre_sessions_total{genre="<id>"}` | Total sessions where songs from this genre were served |
| `genre_engaged_total{genre="<id>"}` | Sessions where the user engaged with a song from this genre |

The **per-genre engagement rate** (`genre_engaged_total / genre_sessions_total`) is continuously observable in Grafana. A diverging rate across genres indicates the model is treating genre groups unequally.

**Drift monitoring:** `pipeline2_retrain/retrain.py` (`detect_drift()`) additionally compares the distribution of `genre_encoded` between incoming feedback and the production baseline at every retrain cycle, logging a `⚠ DRIFT` warning if the mean deviates more than 20%.

**Concrete files:**
- `data/pipelines/generator/generator.py` — Prometheus counters + `start_http_server(8001)`
- `devops/k8s/monitoring/servicemonitor-generator.yaml` — K8s ServiceMonitor scraping `/metrics`
- `data/pipelines/pipeline2_retrain/retrain.py` — `detect_drift()` step 3c

---

## 2. Explainability

**Owner: Training**

**Concern:** The LightGBM ranking model makes decisions based on 7 input features, but without recording which features drive predictions, it is impossible to audit why a particular song is ranked higher than another or to detect if the model is over-relying on a single signal.

**Mechanism implemented:**

After each training run, feature importance scores (`importance_type="gain"`) are logged to MLflow as individual metrics (`feat_importance_<feature_name>`). Gain-based importance measures the total information gain contributed by each feature across all tree splits — a high score means the feature is heavily used in ranking decisions. These scores are visible in the MLflow UI for every registered run, allowing any team member to audit which features matter most and compare how feature reliance shifts across model versions.

**Concrete files:**
- `training/train_ranking_processed.py` — `feat_importance_*` metrics logged to MLflow after every LightGBM training run

---

## 3. Transparency

**Owner: Serving**

**Concern:** Users and operators have no visibility into what the model is recommending in real time, making it impossible to audit ranking decisions or detect unexpected behaviour.

**Mechanisms implemented:**

**User-facing transparency — live session dashboard in Navidrome UI:**
The Navidrome UI polls `GET /active-sessions` every 3 seconds and displays a live table showing:
- Which sessions are currently active
- The user features used for ranking (`user_skip_rate`, `user_favorite_genre_encoded`, `user_watch_time_avg`)
- The ranked song list with `video_id`, `genre_encoded`, and `engagement_probability` for each position

This means any user or operator can see exactly which songs the model ranked and why (which features drove the ranking) at any point in time.

**Operator-facing transparency — Prometheus metrics:**
The serving app exposes a `/metrics` endpoint (scraped by Prometheus every 15s) with:

| Metric | What it shows |
|--------|--------------|
| `http_requests_total` | Request volume by endpoint and status code |
| `http_request_duration_seconds` | Latency distribution (p50/p95/p99) |
| `prediction_score` | Distribution of model output scores (0–1 histogram) |
| `prediction_invalid_total` | Count of out-of-range predictions |
| `smartqueue_active_sessions` | Current number of active sessions |
| `smartqueue_rerank_total` | Total reranking requests served |
| `smartqueue_feedback_skips_total` | User skips after reranking |
| `smartqueue_feedback_completions_total` | User completions after reranking |
| `smartqueue_feedback_songs_kept` | Fraction of ML-ranked order kept by user |

Grafana dashboards (`devops/k8s/monitoring/grafana-dashboards/serving.json`) visualise all of the above in real time.

**Model identity transparency — `/health` endpoint:**
Every request to `/health` returns the exact model version, run URI, and MLflow tracking URI so operators always know which model is serving traffic:
```json
{
  "status": "ok",
  "model_version": "lightgbm_v4",
  "model_uri": "local:/models/smartqueue_lgbm.txt",
  "model_name": "smartqueue-ranking",
  "model_stage": "Production",
  "tracking_uri": "http://129.114.24.226:30500"
}
```

**Concrete files:**
- `serving/lightgbm_app/app.py` — `/active-sessions`, `/metrics`, `/health` endpoints
- `navidrome/server/nativeapi/smartqueue.go` — proxies `/active-sessions` to Navidrome
- `navidrome/ui/src/layout/AppBar.jsx` — session count display in UI
- `devops/k8s/monitoring/servicemonitor-serving.yaml` — K8s ServiceMonitor scraping `/metrics`
- `devops/k8s/monitoring/grafana-dashboards/serving.json` — Grafana dashboard

---

## 4. Privacy

**Owner: Data**

**Concern:** Training data or feedback logs could contain personally identifiable information (PII) that should not be stored or processed without consent.

**Assessment:** The dataset contains **no PII**. Specifically:

- **Source dataset:** XITE Million Sessions Dataset — all user identifiers are opaque session IDs generated by XITE. No names, emails, device IDs, or location data are present in the raw or processed data.
- **Feedback logs:** Each JSONL record contains only `session_id` (a UUID generated per simulated session), `video_id`, rank position, predicted probability, and engagement label. No user-identifying fields are collected or stored.
- **User features:** `user_skip_rate`, `user_favorite_genre_encoded`, `user_watch_time_avg` are computed aggregates over a session — not individual event logs tied to a real person.
- **S3 storage:** Feedback and retrain data stored in `ObjStore_proj13` follow the same schema — no PII at rest.

No anonymisation step is required because no PII enters the pipeline. This should be re-evaluated if real user data (e.g., from a live Navidrome deployment) replaces the simulated production split.

**Concrete files:**
- `data/pipelines/pipeline1_initial/feature_engineering.py` — feature derivation from session aggregates only
- `data/pipelines/generator/generator.py` — feedback schema (no PII fields)

---

## 5. Accountability

**Owner: DevOps**

**Concern:** Model promotions and rollbacks must be traceable so that any decision affecting users can be audited after the fact.

**Mechanism implemented:**

Every model promotion flows through the CT pipeline (Argo Workflows), which provides a complete audit trail:

1. **MLflow** records each training run with `run_id`, hyperparameters, metrics (`val_auc`), and model artifacts. The `run_id` propagates through every subsequent pipeline step — evaluate, deploy-staging, deploy-canary, deploy-prod — so any production model can be traced back to its exact training run.
2. **Argo Workflows** retains the full execution history of every CT pipeline run, including step-level status, duration, logs, and failure reasons. Accessible via the Argo Workflows UI (`http://<floating-ip>:30446`).
3. **ArgoCD** tracks deployment sync history for the platform services, recording what changed, when, and which git commit triggered the sync.
4. **Rollback logging:** `serving/monitoring/promotion_triggers.py` writes structured JSON logs on every rollback event, including the reason, timestamp, and deploy mode.

**Concrete files:**
- `devops/workflows/ct-pipeline.yaml` — run_id passed through all steps from train to deploy
- `serving/monitoring/promotion_triggers.py` — rollback event logging
- Argo Workflows UI (`NodePort 30446`) — pipeline execution history
- ArgoCD UI (`NodePort 30443`) — deployment sync audit

---

## 6. Robustness

**Owner: Serving + DevOps**

**Concern:** A newly deployed model version could degrade serving quality or fail entirely, impacting all users.

**Mechanisms implemented:**

- **Quality gate:** `devops/workflows/ct-pipeline.yaml` (`evaluate-model` step) — the CT pipeline checks `val_auc ≥ 0.65` before allowing promotion. The current production model scores `val_auc = 0.8495`.
- **Canary deployment:** New model versions are deployed to `smartqueue-canary` first and monitored for 30 minutes (6 × 5-minute health + latency checks) before manual approval gates promotion to `smartqueue-prod`.
- **Latency gate:** Canary monitor rejects promotion if `/queue` response time exceeds 2.0 seconds.
- **Manual approval:** `approve-promotion` suspend step in the Argo Workflow requires human sign-off before production deployment.
- **Fallback model loading:** Serving app (`serving/lightgbm_app/app.py`) tries `LOCAL_MODEL_PATH` → MLflow registry → MLflow run URI in order, ensuring a model is always loaded at startup.
- **Automated rollback:** `serving/monitoring/promotion_triggers.py` continuously monitors production metrics (error rate, p95 latency, health endpoint). If thresholds are exceeded (error rate > 2% for 5 min, p95 > 1200ms for 10 min, or 3 consecutive health failures), it triggers `kubectl rollout undo` to revert to the previous deployment revision automatically.

**Concrete files:**
- `devops/workflows/ct-pipeline.yaml` — evaluate-model, canary-monitor, manual-approval steps
- `serving/lightgbm_app/app.py` — model loading fallback chain
- `devops/k8s/monitoring/prometheus-rules.yaml` — `ServingDown` alert

---

## Summary Table

| Principle | Owner | Status | Key File(s) |
|-----------|-------|--------|-------------|
| Fairness | Data | ✅ Implemented | `generator.py`, `servicemonitor-generator.yaml`, `retrain.py` |
| Explainability | Training | ✅ Implemented | `train_ranking_processed.py` |
| Transparency | Serving | ✅ Implemented | `app.py`, `smartqueue.go`, `AppBar.jsx`, `servicemonitor-serving.yaml` |
| Privacy | Data | ✅ Documented | `feature_engineering.py`, `generator.py` |
| Accountability | DevOps | ✅ Implemented | `ct-pipeline.yaml`, `promotion_triggers.py`, Argo Workflows UI, ArgoCD UI |
| Robustness | Serving + DevOps | ✅ Implemented | `ct-pipeline.yaml`, `app.py`, `promotion_triggers.py` |
