#!/bin/bash
# Create K8S secrets for SmartQueue platform services
# Run this ONCE after K8S cluster is up and namespaces are created
# DO NOT commit this file with real values to Git

set -e

NAMESPACE="smartqueue-prod"

echo "=== SmartQueue Secret Creation ==="
echo "Namespace: ${NAMESPACE}"
echo ""

# PostgreSQL
read -p "PostgreSQL user [mlflow]: " PG_USER
PG_USER=${PG_USER:-mlflow}
read -sp "PostgreSQL password: " PG_PASSWORD
echo ""
read -p "PostgreSQL database [mlflow]: " PG_DB
PG_DB=${PG_DB:-mlflow}

kubectl create secret generic postgres-secret \
  --namespace ${NAMESPACE} \
  --from-literal=POSTGRES_USER="${PG_USER}" \
  --from-literal=POSTGRES_PASSWORD="${PG_PASSWORD}" \
  --from-literal=POSTGRES_DB="${PG_DB}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "postgres-secret created."

# Chameleon Object Storage (S3-compatible at CHI@TACC)
# Generate EC2 credentials via Chameleon Horizon GUI or python-chi:
#   Identity > Application Credentials, or use the EC2 credential API
echo ""
echo "Chameleon S3 credentials (from CHI@TACC EC2 credentials):"
read -p "AWS Access Key ID: " S3_ACCESS_KEY
read -sp "AWS Secret Access Key: " S3_SECRET_KEY
echo ""

kubectl create secret generic s3-secret \
  --namespace ${NAMESPACE} \
  --from-literal=AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY}" \
  --from-literal=AWS_SECRET_ACCESS_KEY="${S3_SECRET_KEY}" \
  --from-literal=S3_ACCESS_KEY="${S3_ACCESS_KEY}" \
  --from-literal=S3_SECRET_KEY="${S3_SECRET_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "s3-secret created."

echo ""
echo "=== All secrets created in namespace ${NAMESPACE} ==="
kubectl get secrets -n ${NAMESPACE}
