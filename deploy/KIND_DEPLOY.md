# Deploy to Kind - Instructions

## Deployed (Kind cluster: cloudburst)

- **CloudBroker**: PostgreSQL + API running in `cloudbroker` namespace
- **Coder**: PostgreSQL + Coder running in `coder` namespace
- **Tailscale operator**: Not installed (requires OAuth credentials - configure separately)

## Access (NodePort)

- **CloudBroker**: http://localhost:30080 (NodePort 30080)
- **Coder**: http://localhost:30180 (NodePort 30180)

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
  - containerPort: 30180
    hostPort: 30180

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

## Deploy Coder (CloudNative-PG)

Coder uses CloudNative-PG for PostgreSQL. See [CODER_CNPG_DEPLOY.md](CODER_CNPG_DEPLOY.md).

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

## Optional: Use PVC for Persistent Data

If you have enough disk space and want persistent PostgreSQL data, edit `postgres-minimal.yaml` to use a PVC instead of emptyDir (see git history for the PVC version).
