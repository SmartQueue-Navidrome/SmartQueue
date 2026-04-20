#!/bin/bash
# SmartQueue Full Deployment — Infrastructure + Services
# Provisions a 3-node K8S cluster on Chameleon Cloud and deploys all services.
#
# Prerequisites:
#   - Chameleon Cloud account with active KVM@TACC lease
#   - Local tools: terraform, ansible, kubectl, helm
#   - SSH key registered on Chameleon
#   - devops/tf/kvm/terraform.tfvars configured
#
# Usage:
#   cp .env.example .env   # fill in credentials
#   ./deploy.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Load .env if present
if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a; source "$SCRIPT_DIR/.env"; set +a
fi
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_rsa_chameleon}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

step() { echo ""; echo "====== $1 ======"; }

# --------------- Phase 1: Terraform ---------------
step "Phase 1: Provisioning infrastructure (Terraform)"
cd "$SCRIPT_DIR/devops/tf/kvm"

if [ ! -f terraform.tfvars ]; then
  echo "ERROR: devops/tf/kvm/terraform.tfvars not found."
  echo "Copy terraform.tfvars.example and fill in your Chameleon lease details."
  exit 1
fi

terraform init -input=false
terraform apply -auto-approve

FLOATING_IP=$(terraform output -raw floating_ip)
echo "Floating IP: $FLOATING_IP"
export FLOATING_IP

cd "$SCRIPT_DIR"

# Update Ansible inventory with new floating IP
sed -i.bak "s/ansible_host=[0-9.]*/ansible_host=$FLOATING_IP/; \
  s/cc@[0-9.]*\"/cc@$FLOATING_IP\"/" \
  devops/ansible/inventory/hosts.ini
echo "Ansible inventory updated with $FLOATING_IP"

# Wait for SSH to become available
echo "Waiting for SSH on $FLOATING_IP..."
for i in $(seq 1 30); do
  ssh $SSH_OPTS -i "$SSH_KEY" cc@"$FLOATING_IP" "echo SSH ready" 2>/dev/null && break
  sleep 10
done

# --------------- Phase 2: Ansible pre-K8S ---------------
step "Phase 2: Configuring nodes (Ansible)"
cd "$SCRIPT_DIR/devops/ansible"
ansible-playbook -i inventory/hosts.ini playbooks/pre_k8s.yaml
ansible-playbook -i inventory/hosts.ini playbooks/setup_storage.yaml

# --------------- Phase 3: Kubespray ---------------
step "Phase 3: Deploying Kubernetes (Kubespray)"

# Update kubespray hosts.yaml with floating IP
KUBESPRAY_HOSTS="$SCRIPT_DIR/devops/kubespray/inventory/smartqueue/hosts.yaml"
sed -i.bak "s/access_ip: [0-9.]*/access_ip: $FLOATING_IP/" "$KUBESPRAY_HOSTS"

cd "$SCRIPT_DIR/devops/kubespray-release"
ansible-playbook -i ../kubespray/inventory/smartqueue/hosts.yaml \
  --become \
  cluster.yml

# Verify
ssh $SSH_OPTS -i "$SSH_KEY" cc@"$FLOATING_IP" "kubectl get nodes"

# --------------- Phase 4: Post-K8S setup ---------------
step "Phase 4: Post-K8S setup (Docker, Helm, ArgoCD, Argo Workflows)"
cd "$SCRIPT_DIR/devops/ansible"
ansible-playbook -i inventory/hosts.ini playbooks/post_k8s.yaml

# --------------- Phase 5+: Services ---------------
step "Phase 5: Deploying all services"
cd "$SCRIPT_DIR"
export SSH_KEY FLOATING_IP
./deploy-services.sh
