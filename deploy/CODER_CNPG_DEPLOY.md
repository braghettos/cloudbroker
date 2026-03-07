# Coder with CloudNative-PG

Coder uses CloudNative-PG for PostgreSQL instead of the minimal postgres deployment.

## Prerequisites

- CloudNative-PG operator installed: `kubectl apply --server-side -f https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.28/releases/cnpg-1.28.1.yaml`

## Deploy

```bash
# 1. Create namespace and password secret
kubectl create namespace coder 2>/dev/null || true
kubectl create secret generic coder-db-app -n coder --from-literal=password=coder

# 2. Deploy CloudNative-PG cluster
kubectl apply -f deploy/coder-postgres-cnpg.yaml

# 3. Wait for cluster ready (service coder-db-rw will be created)
kubectl wait --for=condition=Ready cluster/coder-db -n coder --timeout=300s

# 4. Create Coder DB URL secret (use same password as coder-db-app)
kubectl create secret generic coder-db-url -n coder \
  --from-literal=url="postgres://coder:coder@coder-db-rw:5432/coder?sslmode=disable" \
  --dry-run=client -o yaml | kubectl apply -f -

# 5. Install Coder
helm install coder coder-v2/coder -n coder -f deploy/coder-values.yaml
```

## Migrate from minimal postgres

If Coder was deployed with the minimal postgres, migrate:

```bash
# 1. Uninstall Coder (keeps PVC if any)
helm uninstall coder -n coder

# 2. Remove old postgres
kubectl delete deployment coder-postgres -n coder
kubectl delete svc coder-postgres -n coder

# 3. Follow Deploy steps above
```
