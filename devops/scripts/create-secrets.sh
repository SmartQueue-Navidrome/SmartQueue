#!/bin/bash
# Create K8S secrets for SmartQueue platform services
# Run this ONCE after K8S cluster is up and namespaces are created
# DO NOT commit this file with real values to Git

set -e

NAMESPACES="smartqueue-platform smartqueue-staging smartqueue-canary smartqueue-prod"

echo "=== SmartQueue Secret Creation ==="
echo "Namespaces: ${NAMESPACES}"
echo ""

# PostgreSQL
read -p "PostgreSQL user [mlflow]: " PG_USER
PG_USER=${PG_USER:-mlflow}
read -sp "PostgreSQL password: " PG_PASSWORD
echo ""
read -p "PostgreSQL database [mlflow]: " PG_DB
PG_DB=${PG_DB:-mlflow}

# Chameleon Object Storage (S3-compatible at CHI@TACC)
echo ""
echo "Chameleon S3 credentials (from CHI@TACC EC2 credentials):"
read -p "AWS Access Key ID: " S3_ACCESS_KEY
read -sp "AWS Secret Access Key: " S3_SECRET_KEY
echo ""

for NS in ${NAMESPACES}; do
  echo ""
  echo "--- Creating secrets in namespace: ${NS} ---"

  kubectl create secret generic postgres-secret \
    --namespace ${NS} \
    --from-literal=POSTGRES_USER="${PG_USER}" \
    --from-literal=POSTGRES_PASSWORD="${PG_PASSWORD}" \
    --from-literal=POSTGRES_DB="${PG_DB}" \
    --dry-run=client -o yaml | kubectl apply -f -

  kubectl create secret generic s3-secret \
    --namespace ${NS} \
    --from-literal=AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY}" \
    --from-literal=AWS_SECRET_ACCESS_KEY="${S3_SECRET_KEY}" \
    --from-literal=S3_ACCESS_KEY="${S3_ACCESS_KEY}" \
    --from-literal=S3_SECRET_KEY="${S3_SECRET_KEY}" \
    --dry-run=client -o yaml | kubectl apply -f -

  echo "Secrets created in ${NS}."
done

echo ""
echo "=== All secrets created ==="
for NS in ${NAMESPACES}; do
  echo "--- ${NS} ---"
  kubectl get secrets -n ${NS}
done
