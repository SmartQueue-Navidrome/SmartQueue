# Phase 2 — Task Breakdown by Role

> Deadline: Apr 20 (demo) / Apr 27 (final freeze)
> Team size: 4 people → requires Kubernetes + staging/canary/production environments


---

## Joint Responsibilities (12/15 pts)

These tasks require coordination across roles. Ownership is assigned to whoever is best positioned, but everyone needs to be aware.

### J1. End-to-end pipeline automation
**Owner: DevOps (lead) + all roles**

- [ ] Full path runs without human intervention: production data → inference → feedback → retrain → eval → deploy
- [ ] Retrain triggered automatically (cron or CI event), no manual SSH
- [ ] Each stage hands off to the next via S3 / API / CI trigger

### J2. Kubernetes deployment on Chameleon
**Owner: DevOps**

- [ ] All services running on Chameleon in K8s
- [ ] Three environments: staging, canary, production
- [ ] Automated promotion: new model version promoted canary → production if it passes quality gates
- [ ] Automated rollback: if production model degrades, rollback triggered automatically (no human)

### J3. SmartQueue integrated into Navidrome
**Owner: Serving (backend) + Data (generator changes)**

- [ ] SmartQueue page added to Navidrome UI (React sidebar + live session table)
- [ ] Navidrome calls serving's `/active-sessions` every 3s to update the dashboard
- [ ] Generator calls `POST /session/end` after writing feedback
- [ ] Feature is part of the normal user flow (not a separate demo tool)

### J4. Unified infrastructure (no duplication)
**Owner: DevOps (enforce) + all roles (clean up)**

- [ ] Single MLflow instance shared by all roles
- ✅ Single S3 bucket (`ObjStore_proj13`)
- [ ] Single Docker Compose / K8s manifests repo, not per-role copies
- [ ] Clean up unused security groups, buckets, or infra from earlier stages

### J5. Repository structure
**Owner: DevOps (lead) + all roles**

- ✅ Repo (or multi-repo with clear boundaries) makes the full system understandable
- [ ] README explains how to deploy, run, and reproduce the whole system
- ✅ No "this part only works on my machine" leftovers

### J6. Safeguarding plan
**Owner: all roles contribute, training leads the doc**

- [ ] Written plan covering: fairness, explainability, transparency, privacy, accountability, robustness
- [ ] Concrete mechanisms implemented in the system (not just documentation)

Suggested ownership per principle:

| Principle | Suggested Owner | Example Mechanism |
|-----------|-----------------|-------------------|
| Fairness | Data | Monitor engagement rates across genre groups |
| Explainability | Training | Log feature importance per model version in MLflow |
| Transparency | Serving | `/active-sessions` shows what the model is recommending in real time |
| Privacy | Data | No PII in feedback/training data; user_id hashed |
| Accountability | DevOps | All model promotions and rollbacks logged with reason |
| Robustness | Serving | Fallback to last stable model if new version fails health check |

---

## Summary Checklist

| # | Item | Role | Done? |
|---|------|------|-------|
| J1 | End-to-end automation | DevOps + all | [ ] |
| J2 | K8s on Chameleon (staging/canary/prod) | DevOps | [ ] |
| J3 | SmartQueue in Navidrome | Serving + Data | [ ] |
| J4 | Unified infra, no duplication | DevOps + all | [ ] |
| J5 | Repo structure + README | DevOps + all | [ ] |
| J6 | Safeguarding plan + implementation | all | [ ] |
