# Local Testing Guide for CloudBroker

This document describes how to run and test CloudBroker on your local machine
using Docker Compose (quickest path) or a Kind-based Kubernetes cluster (closer
to production).

---

## Prerequisites

| Tool | Minimum version | Purpose |
|------|----------------|---------|
| Docker | 24+ | Container runtime |
| docker-compose | v2 (bundled with Docker Desktop) | Local stack orchestration |
| Python | 3.11+ | Running the API and ingestor outside containers |
| Kind | 0.20+ | Local Kubernetes cluster (Option 2 only) |
| kubectl | 1.28+ | Kubernetes CLI (Option 2 only) |
| Helm | 3.13+ | Chart installation (Option 2 only) |

Make sure the Python virtual environment is set up before proceeding:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[ingest]"
```

---

## Option 1: Docker Compose (fastest)

This option runs PostgreSQL in a container while you start the API on the host.
It is the fastest way to iterate on code changes.

### 1. Start the database

```bash
docker compose up -d db
```

Wait for the health check to pass:

```bash
docker compose ps   # STATE should show "healthy"
```

### 2. Run database migrations

```bash
DATABASE_URL_SYNC=postgresql://postgres:postgres@localhost:5432/cloudbroker \
  .venv/bin/python -m alembic upgrade head
```

### 3. Ingest provider data

Start with providers that do not require API keys:

```bash
.venv/bin/python -m apps.ingestor ingest --provider contabo
.venv/bin/python -m apps.ingestor ingest --provider azure --quick
```

For providers that require credentials, export the relevant environment
variables first (see `.env.example` for the full list):

```bash
export HETZNER_API_TOKEN=your-token-here
.venv/bin/python -m apps.ingestor ingest --provider hetzner
```

### 4. Start the API server

```bash
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/cloudbroker \
  APP_ENV=development \
  .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The `--reload` flag watches for file changes and restarts the server
automatically.

### 5. Verify the API is running

```bash
curl http://localhost:8000/health
```

### 6. Test a recommendation request

```bash
curl -X POST http://localhost:8000/api/recommendations \
  -H "Content-Type: application/json" \
  -d '{
    "min_vcpu": 2,
    "min_ram_gb": 4,
    "arch": "x86_64",
    "max_price_eur_per_hour": 0.10,
    "region_constraint": "EU"
  }'
```

---

## Option 2: Kind Cluster with Helm Chart

This option creates a local Kubernetes cluster with Kind, deploys PostgreSQL
inside the cluster, and installs CloudBroker via its Helm chart.

### 1. Create the Kind cluster

The repository ships a Kind configuration that maps NodePort 30080 to the host:

```bash
kind create cluster --config deploy/kind-config.yaml --name cloudbroker
```

### 2. Build and load the container image

```bash
docker build -f docker/Dockerfile -t cloudbroker:local .
kind load docker-image cloudbroker:local --name cloudbroker
```

### 3. Create the namespace

```bash
kubectl create namespace cloudbroker
```

### 4. Deploy PostgreSQL

The repository includes a CloudNative-PG manifest at
`deploy/postgresql-cluster.yaml`. If you have the CloudNative-PG operator
installed, use it:

```bash
# Install the CloudNative-PG operator (if not already present)
kubectl apply --server-side -f \
  https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.22/releases/cnpg-1.22.1.yaml

# Create the database password secret
kubectl create secret generic cloudbroker-db-app -n cloudbroker \
  --from-literal=password=postgres

# Deploy the PostgreSQL cluster
kubectl apply -f deploy/postgresql-cluster.yaml
```

Alternatively, for a simpler setup without the operator, deploy a plain
PostgreSQL pod:

```bash
kubectl apply -n cloudbroker -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cloudbroker-db
spec:
  replicas: 1
  selector:
    matchLabels:
      app: cloudbroker-db
  template:
    metadata:
      labels:
        app: cloudbroker-db
    spec:
      containers:
      - name: postgres
        image: postgres:16-alpine
        env:
        - name: POSTGRES_USER
          value: postgres
        - name: POSTGRES_PASSWORD
          value: postgres
        - name: POSTGRES_DB
          value: cloudbroker
        ports:
        - containerPort: 5432
        readinessProbe:
          exec:
            command: ["pg_isready", "-U", "postgres"]
          initialDelaySeconds: 5
          periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: cloudbroker-db
spec:
  selector:
    app: cloudbroker-db
  ports:
  - port: 5432
    targetPort: 5432
EOF
```

Wait until the database pod is ready:

```bash
kubectl wait --for=condition=ready pod -l app=cloudbroker-db -n cloudbroker --timeout=60s
```

### 5. Install the Helm chart

**Option A -- Using an existing Secret (recommended):**

```bash
kubectl create secret generic cloudbroker-secrets -n cloudbroker \
  --from-literal=database_url='postgresql+asyncpg://postgres:postgres@cloudbroker-db:5432/cloudbroker' \
  --from-literal=database_url_sync='postgresql://postgres:postgres@cloudbroker-db:5432/cloudbroker'

helm install cloudbroker helm/cloudbroker/ -n cloudbroker \
  --set image.repository=cloudbroker \
  --set image.tag=local \
  --set image.pullPolicy=Never \
  --set existingSecret=cloudbroker-secrets \
  --set config.appEnv=development
```

**Option B -- Using inline secrets (simpler, development only):**

```bash
helm install cloudbroker helm/cloudbroker/ -n cloudbroker \
  --set image.repository=cloudbroker \
  --set image.tag=local \
  --set image.pullPolicy=Never \
  --set secrets.database_url='postgresql+asyncpg://postgres:postgres@cloudbroker-db:5432/cloudbroker' \
  --set secrets.database_url_sync='postgresql://postgres:postgres@cloudbroker-db:5432/cloudbroker' \
  --set config.appEnv=development
```

### 6. Verify the deployment

```bash
kubectl get pods -n cloudbroker
kubectl logs deployment/cloudbroker -n cloudbroker
```

### 7. Access the API

Port-forward the service to your host:

```bash
kubectl port-forward svc/cloudbroker 8000:8000 -n cloudbroker
```

Then in another terminal:

```bash
curl http://localhost:8000/health
```

---

## Running Ingestion in Kubernetes

The Helm chart creates a CronJob (`cloudbroker-ingest`) that runs daily at
04:00 UTC by default. To trigger it manually:

```bash
kubectl create job --from=cronjob/cloudbroker-ingest cloudbroker-ingest-manual -n cloudbroker
kubectl logs job/cloudbroker-ingest-manual -n cloudbroker -f
```

To run ingestion for a single provider, override the command:

```bash
kubectl create job cloudbroker-ingest-contabo -n cloudbroker \
  --image=cloudbroker:local \
  -- python -m apps.ingestor ingest --provider contabo
```

---

## Running Unit Tests

The test suite includes mocked connector tests that do not require a running
database or network access:

```bash
.venv/bin/python -m pytest tests/test_connectors_mocked.py tests/test_connector_parsing.py -v
```

To run the full test suite (requires a running PostgreSQL instance):

```bash
DATABASE_URL_SYNC=postgresql://postgres:postgres@localhost:5432/cloudbroker \
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/cloudbroker \
  .venv/bin/python -m pytest tests/ -v
```

---

## Verifying Recommendations End-to-End

Once the API is running and at least one provider has been ingested, you can
test the recommendation engine.

### Send a recommendation request

```bash
curl -s -X POST http://localhost:8000/api/recommendations \
  -H "Content-Type: application/json" \
  -d '{
    "min_vcpu": 2,
    "min_ram_gb": 4,
    "arch": "x86_64",
    "max_price_eur_per_hour": 0.10,
    "region_constraint": "EU"
  }' | python -m json.tool
```

### Interpreting the response

The response contains a ranked list of VM offers. Key fields to check:

- **score** -- Overall ranking score (higher is better). Combines price
  efficiency, provider reliability, and regional fit.
- **explain** -- Breakdown of how the score was computed. Shows the weight of
  each factor (price, performance, sustainability, region match) so you can
  understand why one offer ranks above another.
- **tco** -- Total Cost of Ownership estimate. Projects the monthly and yearly
  cost based on the hourly price, helping you compare long-term spend across
  providers.
- **provider** / **instance_type** -- Identifies the cloud provider and the
  specific VM SKU.
- **region** -- The data-centre region where the offer is available.
- **vcpu**, **ram_gb**, **price_eur_per_hour** -- The hardware specs and unit
  price that matched your constraints.

If the response is empty, make sure you have ingested at least one provider and
that the ingested data includes offers matching your constraints (for example,
lower the `min_vcpu` or raise `max_price_eur_per_hour`).

---

## Cleanup

Remove the Kind cluster and all its resources:

```bash
kind delete cluster --name cloudbroker
```

Tear down the Docker Compose stack and remove the database volume:

```bash
docker compose down -v
```
