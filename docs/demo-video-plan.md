# Demo Video Plan

> Length: 20–30 minutes
> Recording: Each team member records their own parts separately
> Video compilation: Training

---

## Role Assignment Overview

| Part | Content | Owner | Notes |
|------|---------|-------|-------|
| Part 1 | System Bring-up | DevOps | |
| Part 2 | ML Feature in Navidrome | Data | |
| Part 3 | Production Data Script, Feedback Capture & Storage | Data | |
| Part 4 | Retraining & Redeployment | DevOps | |
| Part 5 | Per-Role Monitoring & Safeguarding & Bonus | Each role individually | |

---

## Part 1 — System Bring-up (DevOps)

**Goal:** Show that the system is fully deployed and all services are running. No need to re-deploy from scratch.

**Narration:** The system is deployed on 3 KVM nodes on Chameleon Cloud using Terraform, Ansible, and Kubespray.

### Steps

1. SSH into node1
   ```bash
   ssh cc@129.114.24.226
   ```

2. Verify all K8s nodes are ready
   ```bash
   kubectl get nodes
   ```

3. Verify all pods are Running across namespaces
   ```bash
   kubectl get pods -n smartqueue-platform   # PostgreSQL, MLflow, Redis, Navidrome
   kubectl get pods -n smartqueue-prod       # Serving + Generator
   kubectl get pods -n argocd
   kubectl get pods -n argo
   kubectl get pods -n monitoring            # Grafana, Prometheus
   ```

4. Open each UI to confirm it is accessible
   - Navidrome: `http://129.114.24.226:30453`
   - MLflow: `http://129.114.24.226:30500`
   - ArgoCD: `http://129.114.24.226:30443`
   - Argo Workflows: `http://129.114.24.226:30446`
   - Grafana: `http://129.114.24.226:30300`

5. Briefly explain the role of each service (~30 seconds)

---

## Part 2 — ML Feature in Navidrome (Data)

**Goal:** Show how the SmartQueue ML feature is integrated into Navidrome (the open source service).

### Steps

1. Open the Navidrome UI and point to the green "N active sessions" badge in the AppBar — explain that these are sessions currently being ranked by the ML model

2. Navigate to the SmartQueue Live Sessions dashboard and explain each column:
   - **Session**: the active session ID
   - **Rank**: the position of each song within the session (1/2/3)
   - **Video ID**: the song being ranked
   - **Genre Encoded**: the genre of the song
   - **Score**: LightGBM predicted engagement probability (green = high, red = low)

3. Explain that the dashboard auto-refreshes every 3 seconds, showing live ML ranking results in real time

---

## Part 3 — Production Data Script, Feedback Capture & Storage (Data)

**Goal:** Show how the Generator simulates real production traffic, how feedback is captured, and the complete S3 data flow from feedback to retrain dataset.

**Narration:** The production split is a time-based subset of the XITE dataset, split by session_id to simulate real user behaviour and prevent data leakage.

### Steps

1. Show the Generator is running
   ```bash
   kubectl logs -f deploy/smartqueue-generator -n smartqueue-prod
   ```
   Let the logs run for a few seconds — show sessions being processed (calling `/queue` and `/session/end`)

2. Show the feedback folder on S3
   - Open https://chi.tacc.chameleoncloud.org/project/containers/container/ObjStore_proj13
   - Confirm the `feedback/{date}/` folder exists

3. Open a JSONL file and explain each field:
   - **session_id**: the ID of this simulated session
   - **video_id**: the song being evaluated
   - **rank**: the song's position in the ML ranking
   - **predicted_prob**: LightGBM predicted engagement probability
   - **engagement**: actual outcome (1 = listened, 0 = skipped)

4. Show the retrain folder on S3, explain that Pipeline2 compiles feedback into a retrain dataset
   - Open https://chi.tacc.chameleoncloud.org/project/containers/container/ObjStore_proj13
   - Confirm `retrain/v{date}/train.parquet` and `metadata.json` exist

5. Open `metadata.json` and show feedback count, label distribution, and drift results

### Data Quality Checkpoints (D1–D3)

- **D1 — Ingestion check:** Pipeline1 runs Great Expectations validation on all 4 splits at ingestion time (row count, required columns, no nulls, label ratio). Hard fail — pipeline stops before S3 upload if checks fail.

- **D2 — Training set compilation check:** Pipeline2 validates feedback format and compiled retrain dataset (row count, label distribution) before saving to S3.

- **D3 — Live inference quality + drift monitoring:** Serving validates every `/queue` request via Pydantic (invalid requests return 422 and increment `invalid_request_total`). At each retrain cycle, `detect_drift()` compares the `genre_encoded` distribution of incoming feedback against the production baseline — logs a warning if deviation exceeds 20%, but does not block the pipeline (drift is a monitoring signal, not a hard blocker).

---

## Part 4 — Retraining & Redeployment (DevOps)

**Goal:** Show the complete model update pipeline, from retrain dataset to new model in production.

### CT Pipeline Flow

```
retrain-data → train-model → evaluate-model (AUC ≥ 0.65)
→ deploy-staging → test-staging
→ deploy-canary → canary-monitor (30 min)
→ manual-approval → deploy-prod
```

### Steps

1. Manually trigger the CT pipeline in Argo Workflows UI
   ```bash
   argo submit -n argo --from cronwf/ct-pipeline
   ```

2. Walk through each step in the Argo Workflows UI

3. Open MLflow UI and show the new run's results:
   - val_auc
   - feature importance (`feat_importance_*`)

4. After the pipeline completes, call `/health` to confirm `model_version` has been updated

---

## Part 5 — Per-Role Monitoring & Safeguarding & Bonus (Each Role)

**Goal:** Each team member shows the monitoring dashboards for their role and explains their safeguarding principles. If a bonus item was implemented, show it here.

### Data (Fairness + Privacy)
- Open the Grafana fairness dashboard — show per-genre engagement rate (`genre_engaged_total / genre_sessions_total`)
- Open `metadata.json` on S3 — show drift detection results
- **Safeguarding — Fairness:** genre engagement rate is continuously monitored to detect if the model systematically favours certain genres over others
- **Safeguarding — Privacy:** the XITE dataset and feedback schema contain no PII — all user identifiers are opaque session IDs
- If a Bonus item was implemented: show it and explain where it is integrated

### Training (Explainability)
- Open MLflow experiment UI — show val_auc trend and multi-run comparison
- Show `feat_importance_*` metrics
- **Safeguarding — Explainability:** after every training run, feature importance (gain) is logged to MLflow so anyone can audit which features drive ranking decisions and how reliance shifts across model versions
- If a Bonus item was implemented: show it and explain where it is integrated

### Serving (Transparency)
- Open Grafana serving dashboard and show:
  - latency p95
  - error rate
  - number of active sessions
  - prediction score distribution
- **Safeguarding — Transparency:** the SmartQueue Live Sessions dashboard lets any user or operator see exactly which songs the model ranked and with what score, in real time
- If a Bonus item was implemented: show it and explain where it is integrated

### DevOps (Accountability + Robustness)
- Open Grafana K8s cluster dashboard (node CPU/memory, pod status)
- Show Argo Workflows execution history (full audit trail for every CT pipeline run)
- Show ArgoCD app sync status
- **Safeguarding — Accountability:** every model promotion carries a `run_id` that traces back through the full pipeline history — train, evaluate, deploy steps are all logged
- **Safeguarding — Robustness:** canary monitor + automated rollback (`promotion_triggers.py`) protect production from a bad model version
- If a Bonus item was implemented: show it and explain where it is integrated
