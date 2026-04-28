#!/bin/bash
# SmartQueue full deployment script — from new Chameleon lease to running services.
#
# Prerequisites:
#   - terraform, ansible, ssh installed locally
#   - ~/.config/openstack/clouds.yaml configured
#   - SSH key ~/.ssh/id_rsa_chameleon registered on KVM@TACC
#   - Chameleon lease created with 3x m1.large instances
#
# Usage:
#   cd SmartQueue/devops
#   bash scripts/deploy.sh <reservation_id>
#
# Or run individual phases:
#   bash scripts/deploy.sh <reservation_id> --phase terraform
#   bash scripts/deploy.sh <reservation_id> --phase ansible
#   bash scripts/deploy.sh <reservation_id> --phase kubespray
#   bash scripts/deploy.sh <reservation_id> --phase services
#   bash scripts/deploy.sh --floating-ip <IP> --phase kubespray   # skip terraform

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEVOPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_KEY="$HOME/.ssh/id_rsa_chameleon"
SSH_USER="cc"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i $SSH_KEY"

# --- Parse arguments ---
RESERVATION_ID=""
FLOATING_IP=""
PHASE="all"
DEMO=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --phase) PHASE="$2"; shift 2 ;;
        --floating-ip) FLOATING_IP="$2"; shift 2 ;;
        --demo) DEMO=true; shift ;;
        --help|-h)
            echo "Usage: $0 [reservation_id] [--phase PHASE] [--floating-ip IP] [--demo]"
            echo "Phases: all, terraform, ansible, kubespray, services"
            echo "  --demo   Pause between steps for video recording"
            exit 0 ;;
        *) RESERVATION_ID="$1"; shift ;;
    esac
done

ssh_node1() { ssh $SSH_OPTS ${SSH_USER}@${FLOATING_IP} "$@"; }

log() { echo ""; echo "========== $1 =========="; }

# In demo mode, pause and show what to narrate
pause() {
    if [ "$DEMO" = true ]; then
        echo ""
        echo "─── PAUSE ─── $1"
        echo "    $2"
        read -p "    Press Enter to continue..."
        echo ""
    fi
}

# ============================================================
# PHASE 1: TERRAFORM
# ============================================================
phase_terraform() {
    log "Phase 1: Terraform"

    if [ -z "$RESERVATION_ID" ]; then
        echo "ERROR: reservation_id required for terraform phase"
        exit 1
    fi

    cd "$DEVOPS_DIR/tf/kvm"

    # Update reservation_id in tfvars
    sed -i.bak "s/reservation_id = .*/reservation_id = \"${RESERVATION_ID}\"/" terraform.tfvars
    echo "Updated terraform.tfvars with reservation_id=${RESERVATION_ID}"

    pause "Terraform plan" "Show: devops/tf/kvm/instances.tf, network.tf, volumes.tf"

    terraform init
    terraform plan -var-file=terraform.tfvars
    terraform apply -var-file=terraform.tfvars -auto-approve

    FLOATING_IP=$(terraform output -raw floating_ip)
    echo "Floating IP: $FLOATING_IP"

    # Auto-generate inventory files
    bash "$SCRIPT_DIR/generate-config.sh" "$FLOATING_IP"

    # Show volume devices (user may need to update setup_storage.yaml)
    echo ""
    echo "Volume devices:"
    terraform output volume_devices
    echo "If device paths differ from /dev/vdb,/dev/vdc, update ansible/playbooks/setup_storage.yaml"

    pause "Terraform done" "Show: generated hosts.ini + kubespray hosts.yaml. Explain auto-config generation."

    cd "$DEVOPS_DIR"
}

# ============================================================
# PHASE 2: ANSIBLE (pre-k8s + NAT + storage)
# ============================================================
phase_ansible() {
    log "Phase 2: Ansible (pre-k8s + NAT + storage)"

    if [ -z "$FLOATING_IP" ]; then
        # Try to read from .env
        if [ -f "$DEVOPS_DIR/.env" ]; then
            source "$DEVOPS_DIR/.env"
        else
            echo "ERROR: No floating IP. Run terraform phase first or pass --floating-ip"
            exit 1
        fi
    fi

    # Clean old host key
    ssh-keygen -R "$FLOATING_IP" 2>/dev/null || true

    # Wait for SSH to be available
    echo "Waiting for SSH on $FLOATING_IP..."
    SSH_READY=false
    for i in $(seq 1 30); do
        if ssh $SSH_OPTS ${SSH_USER}@${FLOATING_IP} 'echo SSH_OK' 2>/dev/null; then
            SSH_READY=true
            break
        fi
        echo "  attempt $i/30..."
        sleep 10
    done
    if [ "$SSH_READY" = false ]; then
        echo "ERROR: Cannot reach $FLOATING_IP after 30 attempts"
        exit 1
    fi

    cd "$DEVOPS_DIR/ansible"

    # SCP the SSH key to node1 (needed for kubespray to reach workers)
    echo "Copying SSH key to node1..."
    scp $SSH_OPTS "$SSH_KEY" ${SSH_USER}@${FLOATING_IP}:~/.ssh/id_rsa_chameleon
    ssh_node1 "chmod 600 ~/.ssh/id_rsa_chameleon"

    pause "Pre-K8S" "Show: playbooks/pre_k8s.yaml — firewall, kernel modules on all nodes"

    # Pre-K8S setup (all nodes: kernel modules, firewall)
    ansible-playbook -i inventory/hosts.ini playbooks/pre_k8s.yaml

    pause "Storage setup" "Show: playbooks/setup_storage.yaml — block volumes → /mnt/smartqueue-data/"

    # Storage setup (node1: format + mount block volumes)
    ansible-playbook -i inventory/hosts.ini playbooks/setup_storage.yaml

    pause "NAT setup (critical)" "Show: playbooks/setup_nat.yaml — workers need internet before kubespray"

    # NAT setup (CRITICAL: before kubespray)
    ansible-playbook -i inventory/hosts.ini playbooks/setup_nat.yaml

    pause "Ansible done" "All nodes configured. Workers have internet via NAT."

    cd "$DEVOPS_DIR"
}

# ============================================================
# PHASE 3: KUBESPRAY (runs on node1)
# ============================================================
phase_kubespray() {
    log "Phase 3: Kubespray (on node1, ~10 min)"

    if [ -z "$FLOATING_IP" ]; then
        if [ -f "$DEVOPS_DIR/.env" ]; then source "$DEVOPS_DIR/.env"; fi
    fi

    # Copy kubespray inventory (includes hosts.yaml + group_vars/) to node1
    echo "Copying kubespray inventory to node1..."
    ssh_node1 "mkdir -p ~/kubespray-inventory"
    scp -r $SSH_OPTS "$DEVOPS_DIR/kubespray/inventory/smartqueue/" \
        ${SSH_USER}@${FLOATING_IP}:~/kubespray-inventory/

    pause "Kubespray (~10 min)" "Show: kubespray/inventory/smartqueue/hosts.yaml + group_vars/k8s_cluster.yml"

    # Run kubespray on node1
    ssh_node1 'bash -s' << 'KUBE_EOF'
set -e

# Install prerequisites
sudo apt-get update -qq
sudo apt-get install -y -qq ansible git python3-pip python3-venv

# Clone SmartQueue repo (for manifests later)
if [ ! -d ~/SmartQueue ]; then
    git clone https://github.com/yanghao13111/SmartQueue.git ~/SmartQueue
fi
cd ~/SmartQueue && git pull && cd ~

# Clone Navidrome fork (for building navidrome image)
if [ ! -d ~/navidrome ]; then
    git clone https://github.com/SmartQueue-Navidrome/navidrome.git ~/navidrome
fi
cd ~/navidrome && git pull && cd ~

# Clone kubespray
if [ ! -d ~/kubespray ]; then
    git clone --branch release-2.26 https://github.com/kubernetes-sigs/kubespray.git ~/kubespray
fi

cd ~/kubespray

# Setup python venv
if [ ! -d venv ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install -q -r requirements.txt
pip install -q ruamel.yaml

# Generate local SSH key for node1 → node1 (localhost)
if [ ! -f ~/.ssh/id_rsa ]; then
    ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa -q -N ""
fi
grep -qF "$(cat ~/.ssh/id_rsa.pub)" ~/.ssh/authorized_keys || \
    cat ~/.ssh/id_rsa.pub >> ~/.ssh/authorized_keys
ssh -o StrictHostKeyChecking=no cc@localhost hostname 2>/dev/null || true

# Copy our inventory (auto-generated with node1=127.0.0.1)
cp -r ~/kubespray-inventory/smartqueue inventory/

# Deploy K8S
ansible-playbook -i inventory/smartqueue/hosts.yaml --become --become-user=root cluster.yml

# Setup kubeconfig
sudo cp -R /root/.kube /home/cc/.kube 2>/dev/null || true
sudo chown -R cc:cc /home/cc/.kube

echo ""
echo "=== Kubespray complete ==="
kubectl get nodes
KUBE_EOF
}

# ============================================================
# PHASE 4: POST-K8S + SECRETS + PLATFORM SERVICES
# ============================================================
phase_services() {
    log "Phase 4: Post-K8S setup + secrets + services"

    if [ -z "$FLOATING_IP" ]; then
        if [ -f "$DEVOPS_DIR/.env" ]; then source "$DEVOPS_DIR/.env"; fi
    fi

    cd "$DEVOPS_DIR/ansible"

    pause "Post-K8S setup" "Show: playbooks/post_k8s.yaml — Docker, registry, Helm, ArgoCD, Argo Workflows"

    # Run post_k8s playbook (Docker, Helm, ArgoCD, Argo Workflows, copy manifests)
    ansible-playbook -i inventory/hosts.ini playbooks/post_k8s.yaml

    cd "$DEVOPS_DIR"

    # Load credentials from .secrets file
    SECRETS_FILE="$DEVOPS_DIR/.secrets"
    if [ ! -f "$SECRETS_FILE" ]; then
        echo ""
        echo "ERROR: $SECRETS_FILE not found."
        echo "Create it with:"
        echo "  cat > $SECRETS_FILE << 'EOF'"
        echo "  POSTGRES_USER=mlflow"
        echo "  POSTGRES_PASSWORD=smartqueue2026"
        echo "  POSTGRES_DB=mlflow"
        echo "  S3_ACCESS_KEY=<your-chameleon-s3-access-key>"
        echo "  S3_SECRET_KEY=<your-chameleon-s3-secret-key>"
        echo "  EOF"
        exit 1
    fi
    source "$SECRETS_FILE"

    pause "Secrets" "Show: devops/.secrets (structure only). Explain: postgres + S3 creds, gitignored."

    # Create secrets on the cluster
    log "Creating K8S secrets"
    ssh_node1 "bash -s" << SECRETS_EOF
set -e

# Create secrets in all namespaces
for ns in smartqueue-platform smartqueue-staging smartqueue-canary smartqueue-prod; do
    kubectl create secret generic postgres-secret -n \$ns \
        --from-literal=POSTGRES_USER=${POSTGRES_USER} \
        --from-literal=POSTGRES_PASSWORD=${POSTGRES_PASSWORD} \
        --from-literal=POSTGRES_DB=${POSTGRES_DB} \
        --dry-run=client -o yaml | kubectl apply -f -
done

for ns in smartqueue-platform smartqueue-staging smartqueue-canary smartqueue-prod argo; do
    kubectl create secret generic s3-secret -n \$ns \
        --from-literal=AWS_ACCESS_KEY_ID=${S3_ACCESS_KEY} \
        --from-literal=AWS_SECRET_ACCESS_KEY=${S3_SECRET_KEY} \
        --from-literal=S3_ACCESS_KEY=${S3_ACCESS_KEY} \
        --from-literal=S3_SECRET_KEY=${S3_SECRET_KEY} \
        --dry-run=client -o yaml | kubectl apply -f -
done

echo "Secrets created in all namespaces"
SECRETS_EOF

    pause "Containerd registry fix" "Show: group_vars/k8s_cluster.yml insecure registry config. Explain fallback."

    # Containerd insecure registry fix (in case kubespray didn't configure it)
    log "Configuring containerd insecure registry"
    ssh_node1 'bash -s' << 'REGISTRY_EOF'
set -e

for target in 127.0.0.1 192.168.1.12 192.168.1.13; do
    if [ "$target" = "127.0.0.1" ]; then
        SSH_CMD="bash -c"
    else
        SSH_CMD="ssh -o StrictHostKeyChecking=no -i /home/cc/.ssh/id_rsa_chameleon cc@$target bash -c"
    fi

    $SSH_CMD '
        # Check if containerd already knows about node1:5000
        if grep -q "node1:5000" /etc/containerd/config.toml 2>/dev/null; then
            echo "containerd already configured on $(hostname)"
            exit 0
        fi

        # Check if config_path is set (mutually exclusive with mirrors)
        if grep -q "config_path" /etc/containerd/config.toml 2>/dev/null; then
            # Use hosts.toml approach (needs [host] block with capabilities)
            sudo mkdir -p /etc/containerd/certs.d/node1:5000
            sudo tee /etc/containerd/certs.d/node1:5000/hosts.toml > /dev/null << HOSTS
server = "http://node1:5000"

[host."http://node1:5000"]
  capabilities = ["pull", "resolve"]
  skip_verify = true
HOSTS
            sudo mkdir -p /etc/containerd/certs.d/192.168.1.11:5000
            sudo tee /etc/containerd/certs.d/192.168.1.11:5000/hosts.toml > /dev/null << HOSTS
server = "http://192.168.1.11:5000"

[host."http://192.168.1.11:5000"]
  capabilities = ["pull", "resolve"]
  skip_verify = true
HOSTS
        else
            # Use mirrors approach
            sudo tee -a /etc/containerd/config.toml > /dev/null << TOML

[plugins."io.containerd.grpc.v1.cri".registry.mirrors."node1:5000"]
  endpoint = ["http://node1:5000"]
[plugins."io.containerd.grpc.v1.cri".registry.mirrors."192.168.1.11:5000"]
  endpoint = ["http://192.168.1.11:5000"]
TOML
        fi

        # Ensure /etc/hosts has node1 entry
        grep -q "node1" /etc/hosts || echo "192.168.1.11 node1" | sudo tee -a /etc/hosts

        sudo systemctl restart containerd
        echo "containerd configured on $(hostname)"
    '
done
REGISTRY_EOF

    pause "Deploy services" "Show: k8s/platform/ manifests (postgres, redis, mlflow, navidrome) + serving/base/deployment.yaml + push-images.sh"

    # Deploy platform services
    log "Deploying platform services"
    ssh_node1 'bash -s' << 'PLATFORM_EOF'
set -e

# Apply ConfigMap for Navidrome (must exist before deployment)
kubectl apply -f /home/cc/k8s/platform/navidrome/configmap.yaml

# Deploy PostgreSQL
kubectl apply -f /home/cc/k8s/platform/postgres/pvc.yaml
kubectl apply -f /home/cc/k8s/platform/postgres/statefulset.yaml
kubectl apply -f /home/cc/k8s/platform/postgres/service.yaml
echo "Waiting for PostgreSQL..."
kubectl wait --for=condition=ready pod -l app=postgres -n smartqueue-platform --timeout=120s

# Deploy Redis
kubectl apply -f /home/cc/k8s/platform/redis/ || true
echo "Waiting for Redis..."
kubectl wait --for=condition=ready pod -l app=redis -n smartqueue-platform --timeout=60s || true

# Build + push images
cd ~/SmartQueue
git pull

echo "Building and pushing images (this takes a few minutes)..."
sudo docker start registry 2>/dev/null || true
sudo bash devops/scripts/push-images.sh node1:5000

# Deploy MLflow
kubectl apply -f /home/cc/k8s/platform/mlflow/deployment.yaml
kubectl apply -f /home/cc/k8s/platform/mlflow/service.yaml
echo "Waiting for MLflow..."
kubectl wait --for=condition=ready pod -l app=mlflow -n smartqueue-platform --timeout=120s

# Deploy Navidrome
kubectl apply -f /home/cc/k8s/platform/navidrome/pvc.yaml
kubectl apply -f /home/cc/k8s/platform/navidrome/deployment.yaml
kubectl apply -f /home/cc/k8s/platform/navidrome/service.yaml
echo "Waiting for Navidrome..."
kubectl wait --for=condition=ready pod -l app=navidrome -n smartqueue-platform --timeout=120s

# Deploy serving (starts with MOCK_ON_MLFLOW_FAIL=true, works without trained model)
kubectl apply -k /home/cc/k8s/serving/overlays/production/
echo "Waiting for serving..."
kubectl wait --for=condition=ready pod -l app=smartqueue-serving -n smartqueue-prod --timeout=180s || true

# Deploy data generator
kubectl apply -f /home/cc/k8s/data/generator-deployment.yaml || true

# Apply CT pipeline CronWorkflow
kubectl apply -f /home/cc/workflows/ct-pipeline.yaml 2>/dev/null || true

# Setup daily docker prune cron
(crontab -l 2>/dev/null | grep -v "docker.*prune"; echo "0 1 * * * sudo docker image prune -af && sudo docker builder prune -af") | crontab -

echo ""
echo "=== Platform services deployed ==="
kubectl get pods -A | grep smartqueue
echo ""
kubectl get svc -A | grep smartqueue
PLATFORM_EOF

    pause "All services deployed!" "Show: browser tabs — Navidrome, MLflow, ArgoCD, Grafana. Run curl health check."

    # Print access URLs
    log "Deployment complete!"
    echo ""
    echo "Access URLs:"
    echo "  Navidrome:      http://${FLOATING_IP}:30453"
    echo "  MLflow:         http://${FLOATING_IP}:30500"
    echo "  ArgoCD:         https://${FLOATING_IP}:30443"
    echo "  Argo Workflows: http://${FLOATING_IP}:30446"
    echo ""
    echo "ArgoCD admin password:"
    ssh_node1 "kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo"
    echo ""
    echo "To train initial model and update serving:"
    echo "  ssh -i $SSH_KEY ${SSH_USER}@${FLOATING_IP}"
    echo "  kubectl create -f /home/cc/k8s/training/job-template.yaml"
}

# ============================================================
# MAIN
# ============================================================

case "$PHASE" in
    all)
        phase_terraform
        phase_ansible
        phase_kubespray
        phase_services
        ;;
    terraform)  phase_terraform ;;
    ansible)    phase_ansible ;;
    kubespray)  phase_kubespray ;;
    services)   phase_services ;;
    *)
        echo "Unknown phase: $PHASE"
        echo "Valid phases: all, terraform, ansible, kubespray, services"
        exit 1
        ;;
esac
