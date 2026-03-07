#!/bin/bash
# Helper to create CloudBroker secrets. Run before: kubectl apply -k deploy/
set -e

NS="${1:-cloudbroker}"

echo "Creating secrets in namespace: $NS"
kubectl create namespace "$NS" 2>/dev/null || true

read -sp "Enter database password for cloudbroker user: " DB_PASS
echo
[ -z "$DB_PASS" ] && { echo "Password required"; exit 1; }

kubectl create secret generic cloudbroker-db-app -n "$NS" \
  --from-literal=password="$DB_PASS" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic cloudbroker-api-secrets -n "$NS" \
  --from-literal=database_url="postgresql+asyncpg://cloudbroker:${DB_PASS}@cloudbroker-db-rw:5432/cloudbroker" \
  --from-literal=database_url_sync="postgresql://cloudbroker:${DB_PASS}@cloudbroker-db-rw:5432/cloudbroker" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Secrets created. Deploy with: kubectl apply -k deploy/"
