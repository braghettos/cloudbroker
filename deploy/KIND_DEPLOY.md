# Deploy to Kind - Instructions

## Deployed (Kind cluster: cloudburst)

- **CloudBroker**: PostgreSQL + API running in `cloudbroker` namespace

## Access (NodePort)

- **CloudBroker**: http://localhost:30080 (NodePort 30080)

For Kind, create the cluster with `extraPortMappings` so NodePorts are reachable on localhost:

```bash
# kind-config.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
  extraPortMappings:
  - containerPort: 30080
    hostPort: 30080

# kind create cluster --name cloudburst --config kind-config.yaml
```

## Free Disk Space (if needed)

Run these to free space before retrying:

```bash
# Prune Docker (removes unused images, containers, networks)
docker system prune -af

# Or more aggressive - prune everything including build cache
docker builder prune -af
docker system prune -af --volumes
```

## Deploy CloudBroker (after freeing space if needed)

```bash
# 1. Ensure Kind cluster exists (or recreate)
kind get clusters
# If needed: kind create cluster --name cloudburst

# 2. Build and load CloudBroker image
cd cloudbroker
docker build -t ghcr.io/braghettos/cloudbroker:latest -f docker/Dockerfile .
kind load docker-image ghcr.io/braghettos/cloudbroker:latest --name cloudburst

# 3. Deploy PostgreSQL (minimal, ephemeral) + CloudBroker API
kubectl create namespace cloudbroker 2>/dev/null || true
kubectl apply -f deploy/postgres-minimal.yaml
kubectl apply -f deploy/api-deployment-bitnami.yaml
kubectl apply -f deploy/api-service.yaml

# 4. Wait for readiness
kubectl wait --for=condition=ready pod -l app=cloudbroker-db -n cloudbroker --timeout=120s
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=cloudbroker -n cloudbroker --timeout=120s

# 5. Verify (NodePort 30080)
curl http://localhost:30080/health
```

## Optional: Bitnami PostgreSQL (alternative to CNPG)

If CloudNative-PG operator has issues, use Bitnami PostgreSQL instead:
```bash
helm install cloudbroker-db bitnami/postgresql -n cloudbroker --create-namespace -f deploy/bitnami-postgres-values.yaml
kubectl apply -f deploy/api-deployment-bitnami.yaml -f deploy/api-service.yaml
```

## Optional: Use PVC for Persistent Data

If you have enough disk space and want persistent PostgreSQL data, edit `postgres-minimal.yaml` to use a PVC instead of emptyDir (see git history for the PVC version).
