# CloudBroker Kubernetes Deployment (Host Cluster)

CloudBroker is deployed as a **shared service** on the host Kubernetes cluster. It is not part of the Coder workspace template—admins deploy it once.

## Prerequisites

- Kubernetes 1.28+
- [CloudNative-PG operator](https://cloudnative-pg.io/documentation/current/installation/) installed
- `kubectl` and `kustomize`

## Quick Start

### 1. Create secrets

**Option A: Interactive script**
```bash
./deploy/scripts/create-secrets.sh
```

**Option B: Manual**
```bash
# Generate a secure password for the database
export DB_PASSWORD=$(openssl rand -base64 24)

# Create the database app user secret (required by CloudNative-PG)
kubectl create namespace cloudbroker
kubectl create secret generic cloudbroker-db-app -n cloudbroker --from-literal=password="$DB_PASSWORD"

# Create the API secrets (use the same password)
kubectl create secret generic cloudbroker-api-secrets -n cloudbroker \
  --from-literal=database_url="postgresql+asyncpg://cloudbroker:$DB_PASSWORD@cloudbroker-db-rw:5432/cloudbroker" \
  --from-literal=database_url_sync="postgresql://cloudbroker:$DB_PASSWORD@cloudbroker-db-rw:5432/cloudbroker"

# Optional: add API key for production auth
# kubectl patch secret cloudbroker-api-secrets -n cloudbroker --type='json' -p='[{"op": "add", "path": "/data/api_key", "value": "'$(echo -n "your-api-key" | base64)'"}]'
```

### 2. Deploy

```bash
kubectl apply -k deploy/
```

### 3. Run migrations and ingest data

Migrations run automatically via an init container when the API pod starts. In cloudbroker, `make deploy` deploys CloudBroker only. For ingestion, run `make deploy-ingest` (creates secret from `.env` and runs the ingest Job).

For providers that need credentials (Hetzner, AWS, GCP, etc.), create a secret before deploy:

```bash
kubectl create secret generic cloudbroker-ingest-credentials -n cloudbroker \
  --from-literal=HETZNER_API_TOKEN=your-token \
  --from-literal=AWS_ACCESS_KEY_ID=your-key \
  --from-literal=AWS_SECRET_ACCESS_KEY=your-secret
# Add other provider env vars as needed
```

To run ingestion manually:

```bash
# Port-forward to access the API
kubectl port-forward -n cloudbroker svc/cloudbroker-service 8000:8000

# From another terminal, run ingestion (requires provider credentials)
# Example: ingest Hetzner
kubectl run ingest-hetzner --rm -it --restart=Never -n cloudbroker \
  --image=ghcr.io/braghettos/cloudbroker:latest \
  --env="DATABASE_URL=postgresql+asyncpg://cloudbroker:YOUR_PASSWORD@cloudbroker-db-rw:5432/cloudbroker" \
  --env="HETZNER_API_TOKEN=your-token" \
  -- python -m apps.ingestor ingest --provider hetzner
```

## Architecture

- **cloudbroker-db**: CloudNative-PG Cluster (PostgreSQL 16, single instance)
- **cloudbroker-api**: FastAPI application
- **cloudbroker-service**: ClusterIP Service on port 8000

Cloudburst instances in vclusters connect to `http://cloudbroker-service.cloudbroker.svc.cluster.local:8000`.

## Image

Default image: `ghcr.io/braghettos/cloudbroker:latest`. Override in the Deployment or via Kustomize `images` if using a different registry.
