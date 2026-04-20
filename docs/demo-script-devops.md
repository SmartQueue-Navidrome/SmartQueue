# DevOps Demo Recording Handbook

> **Role:** DevOps
> **Parts:** Part 1 (System Bring-up), Part 4 (CT Pipeline), Part 5 (Monitoring + Safeguarding + Bonus)
> **Estimated time:** 8–10 minutes total
> **Language:** English script provided, deliver in Chinese

---

## Pre-Recording Checklist

Before you start recording, make sure:

- [ ] CT pipeline has been triggered and completed (including manual approval)
- [ ] All pods are Running (no CrashLoopBackOff or Completed leftovers)
- [ ] Browser tabs pre-opened:
  1. Navidrome: `http://129.114.24.226:30453`
  2. MLflow: `http://129.114.24.226:30500`
  3. ArgoCD: `http://129.114.24.226:30443` (login: admin / `kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d`)
  4. Argo Workflows: `http://129.114.24.226:30446`
  5. Grafana: `http://129.114.24.226:30300` (login: admin / smartqueue)
- [ ] Terminal open with SSH to node1: `ssh -i ~/.ssh/id_rsa_chameleon cc@129.114.24.226`

---

## Part 1 — System Bring-up (2–3 min)

### What to show

The entire SmartQueue system is deployed and running on a 3-node Kubernetes cluster on Chameleon Cloud.

### Script + Commands

**[Terminal — SSH into node1]**

```bash
ssh -i ~/.ssh/id_rsa_chameleon cc@129.114.24.226
```

> **Say:** "Our system is deployed on Chameleon Cloud using three KVM virtual machines. We use Terraform to provision the infrastructure — three nodes, a private network, security groups, and block storage volumes. Then Ansible configures the nodes, and Kubespray deploys the Kubernetes cluster. Let me show you the cluster status."

**[Run command]**

```bash
kubectl get nodes
```

> **Say:** "We have three nodes — node1 is the control plane and also a worker, node2 and node3 are worker nodes. All are in Ready state running Kubernetes v1.30."

**[Run command]**

```bash
kubectl get pods -n smartqueue-platform
```

> **Say:** "In the platform namespace, we have PostgreSQL as the metadata backend, MLflow for experiment tracking, and Navidrome — the open-source music streaming service that we're adding ML recommendations to."

**[Run command]**

```bash
kubectl get pods -n smartqueue-prod
```

> **Say:** "In production, we have two serving pods running our LightGBM ranking model — they're load-balanced by Kubernetes with an HPA that can scale up to 8 replicas. We also have the data generator continuously simulating production traffic."

**[Run command]**

```bash
kubectl get pods -n argocd
kubectl get pods -n argo
kubectl get pods -n monitoring
```

> **Say:** "ArgoCD handles GitOps-based deployments, Argo Workflows runs our CI/CD and continuous training pipelines, and the monitoring namespace has Prometheus, Grafana, and AlertManager."

**[Switch to browser — quickly show each UI (spend ~5 seconds on each)]**

1. **Navidrome** (`http://129.114.24.226:30453`)
   > "This is Navidrome, the music streaming service."

2. **MLflow** (`http://129.114.24.226:30500`)
   > "MLflow tracks all our training experiments and model artifacts."

3. **ArgoCD** (`http://129.114.24.226:30443`)
   - Click on "Applications" in the left sidebar
   - You should see 4 apps: `smartqueue-platform`, `smartqueue-prod`, `smartqueue-staging`, `smartqueue-canary`
   > "ArgoCD manages four applications — platform services, and three serving environments: production, staging, and canary."

4. **Argo Workflows** (`http://129.114.24.226:30446`)
   > "Argo Workflows runs our continuous training pipeline, which I'll show in detail next."

5. **Grafana** (`http://129.114.24.226:30300`)
   > "And Grafana provides dashboards for cluster monitoring, serving performance, and fairness metrics."

---

## Part 4 — CT Pipeline: Retraining & Redeployment (3–4 min)

### What to show

The full continuous training pipeline: from retraining to canary deployment to production promotion.

### Script + Commands

> **Say:** "Now let me show how model retraining and redeployment works. We have a continuous training pipeline that runs daily as an Argo CronWorkflow. Let me walk through the pipeline steps."

**[Switch to Argo Workflows UI]**

- URL: `http://129.114.24.226:30446`
- Click **"Workflows"** in the left sidebar
- Click on the most recent `ct-pipeline-manual-*` workflow
- You should see the pipeline DAG/step view

> **Say:** "This is our CT pipeline. It has nine steps in sequence."

**[Point to each step in the UI as you explain]**

> **Say:**
> 1. **"retrain-data"** — merges the latest production feedback from S3 into a new training dataset.
> 2. **"train-model"** — trains a new LightGBM model using this dataset and logs it to MLflow.
> 3. **"evaluate-model"** — this is our quality gate. It checks that the model's validation AUC is at least 0.65. If the model doesn't meet this threshold, the pipeline stops here and the bad model never reaches production.

**[Click on the "evaluate-model" step → click "Logs" tab]**

- You should see output like: `val_auc = 0.84xx` and `PASS: val_auc >= 0.65`

> **Say:** "You can see the quality gate passed — the model achieved a validation AUC of 0.84, well above our 0.65 threshold."

> **Say (continue pointing at steps):**
> 4. **"deploy-staging"** — deploys the new model to our staging environment for initial validation.
> 5. **"test-staging"** — runs automated health checks and smoke tests against the staging deployment.
> 6. **"deploy-canary"** — deploys the model to the canary environment, which receives a small portion of production-like traffic.
> 7. **"canary-monitor"** — this is a 30-minute monitoring window. Every 5 minutes, it checks the canary's health endpoint and measures response latency. If the health check fails or latency exceeds 2 seconds, the pipeline aborts.

**[Click on "canary-monitor" step → click "Logs" tab]**

- You should see output like:
  ```
  Check 1/6 (5 min)... Latency: 0.xxxs, Canary OK. Sleeping 5m...
  Check 2/6 (10 min)...
  ...
  Canary monitoring passed!
  ```

> **Say:** "Here you can see all six canary health checks passed over 30 minutes, with latency well under our 2-second threshold."

> **Say (continue):**
> 8. **"manual-approval"** — this is a suspend step that requires human sign-off. An engineer must explicitly approve promotion to production. This keeps a human in the loop for every model update.
> 9. **"deploy-prod"** — after approval, the new model is deployed to production through ArgoCD.

**[Switch to terminal — verify the new model is serving]**

```bash
curl -s http://129.114.24.226:30800/health | python3 -m json.tool
```

> **Say:** "And we can confirm by hitting the production health endpoint — you can see the model version and run ID match the model we just trained."

**[Optional: Switch to ArgoCD UI]**

- Click on `smartqueue-prod` application
- Show the sync status and history

> **Say:** "ArgoCD also shows the sync history — you can see exactly when each deployment happened and which commit triggered it."

---

## Part 5 — DevOps Monitoring, Safeguarding & Bonus (3–4 min)

### 5A. DevOps Monitoring

**[Switch to Grafana — Cluster Dashboard]**

- URL: `http://129.114.24.226:30300`
- Left sidebar → Dashboards → Search "SmartQueue" → Click **"SmartQueue - Cluster Infrastructure"**

> **Say:** "For infrastructure monitoring, we use Grafana with Prometheus. This is our cluster dashboard showing node-level CPU and memory usage, pod status, and disk utilization across all three nodes."

**[Point to panels briefly]**

- CPU usage panel
- Memory usage panel
- Pod status panel
- Disk usage panel

> **Say:** "We also have automated alert rules in Prometheus."

**[Switch to terminal]**

```bash
kubectl get prometheusrule -n monitoring
```

> **Say:** "Our Prometheus rules include alerts for node disk pressure above 80%, serving endpoint being unreachable, pods crash-looping, and PVC volumes nearing capacity. These alerts fire automatically and notify us through AlertManager."

**[Show disk cleanup automation]**

```bash
kubectl get cronjob -n smartqueue-platform
```

> **Say:** "We also have a daily disk cleanup CronJob that automatically prunes Docker build cache, dangling images, and registry garbage. This is important because our node1 only has 60 gigabytes of disk, and without this, docker images would accumulate and cause disk pressure — which cascades into pod evictions and service outages."

---

### 5B. Safeguarding — Accountability

> **Say:** "Now for safeguarding. I'm responsible for two principles: Accountability and Robustness."

> **Say:** "For Accountability — every model promotion is fully traceable. Let me show you the audit trail."

**[Switch to Argo Workflows UI — show the CT pipeline run]**

> **Say:** "First, every CT pipeline run is recorded in Argo Workflows with complete step-by-step execution history, including logs, duration, and status. You can always go back and audit what happened."

**[Switch to MLflow UI]**

- URL: `http://129.114.24.226:30500`
- Click on experiment **"smartqueue-stage-b"**
- Click on the most recent run

> **Say:** "Second, every training run has a unique run ID in MLflow that records the exact hyperparameters, metrics, and model artifacts. This run ID propagates through the entire CT pipeline — from training to evaluation to staging to canary to production. So any model in production can be traced back to its exact training data and configuration."

**[Switch to ArgoCD UI — show sync history]**

- Click on `smartqueue-prod` → click "History" or "SYNC STATUS"

> **Say:** "Third, ArgoCD records every deployment sync — what changed, when, and which git commit triggered it. Together, these three systems provide a complete accountability chain from training data all the way to production deployment."

---

### 5C. Safeguarding — Robustness

> **Say:** "For Robustness — we have four layers of protection to prevent bad models from reaching users."

> **Say (count on fingers or show in pipeline UI):**

> **"Layer 1: Quality Gate."** The evaluate-model step in the CT pipeline checks that validation AUC exceeds 0.65. Models that don't meet this bar are rejected immediately.

> **"Layer 2: Canary Deployment."** New models go through a 30-minute canary monitoring window with health checks every 5 minutes and a latency threshold of 2 seconds. If anything fails, the pipeline stops.

> **"Layer 3: Manual Approval."** A human must explicitly approve promotion to production. This keeps an engineer in the loop for every model change.

> **"Layer 4: Automated Rollback."**

**[Switch to terminal]**

```bash
kubectl get cronwf prod-health-rollback -n argo
```

> **Say:** "We have a production health rollback CronWorkflow that runs every 5 minutes. It checks the production serving endpoint, and if two or more out of three health checks fail, it automatically rolls back the deployment to the previous version using kubectl rollout undo."

> **Say:** "We also have a more comprehensive monitoring script..."

**[Show the file briefly — can just mention it]**

> **Say:** "...called promotion_triggers.py that monitors error rate, p95 latency, and prediction score drift. If error rate exceeds 2% for more than 5 minutes, or p95 latency exceeds 1200 milliseconds for 10 minutes, it triggers an automatic rollback and logs the event."

---

### 5D. Bonus Items

> **Say:** "For bonus items, we implemented several advanced DevOps features:"

**[Switch to terminal]**

```bash
kubectl get hpa -n smartqueue-prod
```

> **Say:** "First, Horizontal Pod Autoscaling — our production serving deployment automatically scales between 2 and 8 replicas based on CPU utilization. When CPU exceeds 60%, Kubernetes adds more pods to handle the load."

**[Switch to Argo Workflows UI — point to the canary steps]**

> **Say:** "Second, Canary Deployment with progressive rollout — new models go through staging, then canary with 30 minutes of automated monitoring, before reaching production."

**[Switch to ArgoCD UI — show the applications]**

> **Say:** "Third, full GitOps with ArgoCD — all deployments are declarative and version-controlled. The serving deployments use Kustomize overlays for production, staging, and canary environments, each in their own Kubernetes namespace."

> **Say:** "And fourth, the automated disk cleanup and workflow TTL management I mentioned earlier, which keeps the system running reliably over extended periods."

---

## Post-Recording Notes

### If CT pipeline is still running during recording

You can show a previously completed pipeline. If none exist (TTL cleaned them), use the one you triggered and:
- Fast-forward through the canary-monitor wait by explaining "the canary monitor runs for 30 minutes — let me skip ahead to the results"
- Show the logs of completed steps

### How to approve manual-approval step

In Argo Workflows UI:
1. Click on the workflow
2. Find the `approve-promotion` step (will show "Running" with a pause icon)
3. Click on it
4. Click **"Resume"** button

Or via terminal:
```bash
kubectl -n argo patch workflow <WORKFLOW_NAME> \
  --type=merge \
  -p '{"spec":{"suspend":false}}'
```

### Key URLs Quick Reference

| Service | URL | Credentials |
|---------|-----|-------------|
| Navidrome | http://129.114.24.226:30453 | (your account) |
| MLflow | http://129.114.24.226:30500 | (no auth) |
| ArgoCD | http://129.114.24.226:30443 | admin / (from secret) |
| Argo Workflows | http://129.114.24.226:30446 | (no auth) |
| Grafana | http://129.114.24.226:30300 | admin / smartqueue |
| Serving (prod) | http://129.114.24.226:30800 | - |

### ArgoCD password retrieval

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
```
