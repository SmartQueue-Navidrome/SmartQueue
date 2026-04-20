#!/bin/bash
# SmartQueue Service Deployment
# Deploys all services onto an existing K8S cluster.
# Prerequisites: K8S cluster running, kubectl configured, SSH access to node1.
#
# Usage:
#   cp .env.example .env   # fill in credentials
#   ./deploy-services.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Load .env if present
if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a; source "$SCRIPT_DIR/.env"; set +a
fi

# --------------- Configuration ---------------
FLOATING_IP="${FLOATING_IP:?Set FLOATING_IP in .env}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_rsa_chameleon}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
SSH_CMD="ssh $SSH_OPTS -i $SSH_KEY cc@$FLOATING_IP"
REPO_DIR="/home/cc/SmartQueue"

REGISTRY="node1:5000"
IMAGE_TAG="${IMAGE_TAG:-v2}"

step() { echo ""; echo "====== $1 ======"; }

# Check if secrets already exist on cluster
SECRETS_EXIST=$($SSH_CMD "kubectl get secret s3-secret -n smartqueue-prod -o name 2>/dev/null && echo yes || echo no")

if [ "$SECRETS_EXIST" = "yes" ]; then
  echo "Secrets already exist on cluster, skipping credential check."
else
  # Secrets don't exist — credentials are required
  PG_PASSWORD="${PG_PASSWORD:?Secrets not found on cluster. Set PG_PASSWORD in .env}"
  S3_ACCESS_KEY="${S3_ACCESS_KEY:?Secrets not found on cluster. Set S3_ACCESS_KEY in .env}"
  S3_SECRET_KEY="${S3_SECRET_KEY:?Secrets not found on cluster. Set S3_SECRET_KEY in .env}"
fi
PG_USER="${PG_USER:-mlflow}"
PG_DB="${PG_DB:-mlflow}"

# --------------- 0. Sync repo on node1 ---------------
step "Syncing repo on node1"
$SSH_CMD "cd $REPO_DIR && git pull --rebase origin main"

# --------------- 1. Build & push Docker images ---------------
step "Building and pushing Docker images"
$SSH_CMD "cd $REPO_DIR && \
  docker build -t $REGISTRY/smartqueue-mlflow:$IMAGE_TAG \
    -f devops/k8s/platform/mlflow/Dockerfile devops/k8s/platform/mlflow/ && \
  docker build -t $REGISTRY/smartqueue-serving:$IMAGE_TAG \
    -f serving/lightgbm_app/Dockerfile serving/lightgbm_app/ && \
  docker build -t $REGISTRY/smartqueue-training:$IMAGE_TAG \
    -f training/docker/Dockerfile training/ && \
  docker build -t $REGISTRY/smartqueue-data:$IMAGE_TAG \
    -f data/pipelines/generator/Dockerfile data/pipelines/generator/ && \
  docker build -t $REGISTRY/smartqueue-retrain:$IMAGE_TAG \
    -f data/pipelines/pipeline2_retrain/Dockerfile data/pipelines/ && \
  echo 'All images built.' && \
  docker push $REGISTRY/smartqueue-mlflow:$IMAGE_TAG && \
  docker push $REGISTRY/smartqueue-serving:$IMAGE_TAG && \
  docker push $REGISTRY/smartqueue-training:$IMAGE_TAG && \
  docker push $REGISTRY/smartqueue-data:$IMAGE_TAG && \
  docker push $REGISTRY/smartqueue-retrain:$IMAGE_TAG && \
  echo 'All images pushed.' && \
  docker builder prune -af"

# --------------- 2. Create secrets ---------------
if [ "$SECRETS_EXIST" = "yes" ]; then
  step "Secrets already exist, skipping"
else
  step "Creating K8S secrets"
  $SSH_CMD "
    for NS in smartqueue-platform smartqueue-prod smartqueue-staging smartqueue-canary; do
      kubectl create secret generic postgres-secret --namespace \$NS \
        --from-literal=POSTGRES_USER=$PG_USER \
        --from-literal=POSTGRES_PASSWORD=$PG_PASSWORD \
        --from-literal=POSTGRES_DB=$PG_DB \
        --dry-run=client -o yaml | kubectl apply -f -
      kubectl create secret generic s3-secret --namespace \$NS \
        --from-literal=AWS_ACCESS_KEY_ID=$S3_ACCESS_KEY \
        --from-literal=AWS_SECRET_ACCESS_KEY=$S3_SECRET_KEY \
        --from-literal=S3_ACCESS_KEY=$S3_ACCESS_KEY \
        --from-literal=S3_SECRET_KEY=$S3_SECRET_KEY \
        --dry-run=client -o yaml | kubectl apply -f -
    done
    # Copy s3-secret to argo namespace
    kubectl get secret s3-secret -n smartqueue-platform -o yaml \
      | sed 's/namespace: smartqueue-platform/namespace: argo/' \
      | kubectl apply -f -
    # Copy ArgoCD admin secret to argo namespace
    kubectl get secret argocd-initial-admin-secret -n argocd -o yaml \
      | sed 's/namespace: argocd/namespace: argo/' \
      | kubectl apply -f -
    echo 'All secrets created.'
  "
fi

# --------------- 3. Deploy platform services ---------------
step "Deploying platform services (PostgreSQL, MLflow, Redis, Navidrome)"
$SSH_CMD "
  kubectl apply -f ~/k8s/platform/postgres/
  kubectl rollout status statefulset postgres -n smartqueue-platform --timeout=120s
  kubectl apply -f ~/k8s/platform/mlflow/
  kubectl rollout status deployment/mlflow -n smartqueue-platform --timeout=120s
  kubectl apply -f ~/k8s/platform/redis/
  kubectl rollout status deployment/redis -n smartqueue-platform --timeout=60s
  kubectl apply -f ~/k8s/platform/navidrome/
  kubectl apply -f ~/k8s/platform/gateway/ 2>/dev/null || true
  echo 'Platform services deployed.'
"

# --------------- 4. Install monitoring ---------------
step "Installing monitoring stack (Prometheus + Grafana)"
ANSIBLE_DIR="$(cd "$(dirname "$0")/devops/ansible" && pwd)"
ansible-playbook -i "$ANSIBLE_DIR/inventory/hosts.ini" "$ANSIBLE_DIR/playbooks/install_monitoring.yaml"

# Deploy additional monitors and dashboards
$SSH_CMD "
  kubectl apply -f ~/k8s/monitoring/servicemonitor-serving.yaml
  kubectl apply -f ~/k8s/monitoring/servicemonitor-generator.yaml 2>/dev/null || true
  kubectl apply -f ~/k8s/monitoring/prometheus-rules.yaml 2>/dev/null || true
  for dashboard in cluster serving fairness; do
    FILE=~/k8s/monitoring/grafana-dashboards/\${dashboard}.json
    [ -f \"\$FILE\" ] && {
      kubectl create configmap grafana-dashboard-\${dashboard} \
        --from-file=\${dashboard}.json=\$FILE \
        --namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
      kubectl label configmap grafana-dashboard-\${dashboard} \
        grafana_dashboard=1 --namespace monitoring --overwrite
    }
  done
  echo 'Monitoring deployed.'
"

# --------------- 5. Deploy serving via ArgoCD ---------------
step "Deploying serving environments via ArgoCD"
$SSH_CMD "
  ARGOCD_PASS=\$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)
  argocd login argocd-server.argocd.svc.cluster.local --username admin --password \"\$ARGOCD_PASS\" --plaintext 2>/dev/null || \
  argocd login argocd-server.argocd.svc.cluster.local:443 --username admin --password \"\$ARGOCD_PASS\" --insecure --grpc-web
  argocd app sync smartqueue-platform --force 2>/dev/null || true
  argocd app sync smartqueue-staging  --force
  argocd app sync smartqueue-canary   --force
  argocd app sync smartqueue-prod     --force
  echo 'All serving environments synced.'
"

# --------------- 6. Deploy generator ---------------
step "Deploying data generator"
$SSH_CMD "
  kubectl apply -f ~/k8s/data/generator-deployment.yaml
  kubectl rollout status deployment/smartqueue-generator -n smartqueue-prod --timeout=120s
  echo 'Generator deployed.'
"

# --------------- 7. Deploy workflows ---------------
step "Deploying Argo WorkflowTemplates and CronWorkflows"
$SSH_CMD "
  kubectl apply -f ~/workflows/ 2>/dev/null || true
  echo 'Workflows deployed.'
"

# --------------- 8. Verify ---------------
step "Verifying deployment"
$SSH_CMD "
  echo '--- Nodes ---'
  kubectl get nodes
  echo ''
  echo '--- Platform ---'
  kubectl get pods -n smartqueue-platform
  echo ''
  echo '--- Serving (prod) ---'
  kubectl get pods -n smartqueue-prod
  echo ''
  echo '--- Monitoring ---'
  kubectl get pods -n monitoring | grep -E 'grafana|prometheus|alertmanager'
  echo ''
  echo '--- ArgoCD Apps ---'
  kubectl get applications -n argocd 2>/dev/null || true
  echo ''
  echo '--- Health Checks ---'
  curl -sf http://localhost:30800/health && echo ' <- Serving OK' || echo 'Serving not ready yet'
  curl -sf http://localhost:30500/health && echo ' <- MLflow OK'  || echo 'MLflow not ready yet'
  curl -sf http://localhost:30300/api/health && echo ' <- Grafana OK' || echo 'Grafana not ready yet'
"

echo ""
echo "====== Deployment complete ======"
echo "  Serving:  http://$FLOATING_IP:30800"
echo "  MLflow:   http://$FLOATING_IP:30500"
echo "  Grafana:  http://$FLOATING_IP:30300  (admin / smartqueue)"
echo "  ArgoCD:   http://$FLOATING_IP:30443"
echo "  Argo WF:  http://$FLOATING_IP:30446"
echo "  Navidrome: http://$FLOATING_IP:30453"
