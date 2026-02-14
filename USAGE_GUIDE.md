# CloudBroker Usage Guide

This guide shows you how to use the implemented features of CloudBroker.

## 🚀 Quick Start

### 1. Start Services

```bash
# Start Docker services (API + PostgreSQL)
make up

# Run migrations
make migrate
```

### 2. Access the API

- **API Base URL**: `http://localhost:8000`
- **Interactive Docs**: `http://localhost:8000/docs` (Swagger UI)
- **Alternative Docs**: `http://localhost:8000/redoc` (ReDoc)

### 3. Authentication

In **development mode** (when `API_KEY` is not set), API key authentication is bypassed.

In **production**, you need to set `API_KEY` in your `.env` file and include it in requests:

```bash
# Generate a secure API key
openssl rand -hex 32

# Add to .env
API_KEY=your-generated-key-here

# Use in requests
curl -H "X-API-Key: your-generated-key-here" http://localhost:8000/api/providers
```

---

## 📡 API Endpoints

### Health Check

```bash
# No authentication required
curl http://localhost:8000/health

# Response:
{
  "status": "ok",
  "checks": {
    "database": {"status": "ok"}
  }
}
```

### Providers

```bash
curl -H "X-API-Key: dev-bypass" http://localhost:8000/api/providers

# With pagination
curl -H "X-API-Key: dev-bypass" "http://localhost:8000/api/providers?limit=10&offset=0"
```

### Regions

```bash
# List all regions
curl -H "X-API-Key: dev-bypass" http://localhost:8000/api/regions

# Filter by provider
curl -H "X-API-Key: dev-bypass" "http://localhost:8000/api/regions?provider=gcp"
```

### Instance Types

```bash
# List all instance types
curl -H "X-API-Key: dev-bypass" http://localhost:8000/api/instance-types

# Filter by provider
curl -H "X-API-Key: dev-bypass" "http://localhost:8000/api/instance-types?provider=gcp"

# Filter by specs
curl -H "X-API-Key: dev-bypass" \
  "http://localhost:8000/api/instance-types?min_vcpu=4&min_ram_gb=8&arch=x86_64"
```

### Prices

```bash
# List prices
curl -H "X-API-Key: dev-bypass" http://localhost:8000/api/prices

# Filter by provider and region
curl -H "X-API-Key: dev-bypass" \
  "http://localhost:8000/api/prices?provider=gcp&region=europe-west1"
```

### Recommendations ⭐

Get instance recommendations based on your requirements:

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-bypass" \
  -d '{
    "min_vcpu": 2,
    "min_ram_gb": 4,
    "arch": "x86_64",
    "region_constraint": "EU",
    "max_price_eur_per_hour": 1.0,
    "purchase_model": "on_demand",
    "limit": 10
  }' \
  http://localhost:8000/api/recommendations
```

**Request Parameters**:
- `min_vcpu` (required): Minimum vCPUs needed
- `min_ram_gb` (required): Minimum RAM in GB
- `arch` (required): Architecture (`x86_64` or `arm64`)
- `region_constraint` (optional): `EU` or `ANY` (default: `ANY`)
- `max_price_eur_per_hour` (required): Maximum price per hour in EUR
- `purchase_model` (optional): `on_demand` or `spot` (default: `on_demand`)
- `preferred_providers` (optional): List of provider slugs to prefer (e.g. `["hetzner", "gcp"]`); preferred providers get a score boost
- `allowed_providers` (optional): List of provider slugs to restrict results to (e.g. `["hetzner", "gcp", "scaleway"]`); when set, only offers from these providers are returned
- `excluded_providers` (optional): List of provider slugs to exclude from results
- `limit` (optional): Max recommendations to return (default: 10, max: 100)

**Response** includes:
- Ranked recommendations with scores
- Price breakdown
- Resource fit analysis
- Region information

### Metrics (Prometheus)

```bash
# Prometheus metrics endpoint
curl http://localhost:8000/api/metrics
```

---

## 🔄 Data Ingestion

### GCP Ingestion

**Prerequisites**: Google Cloud Application Default Credentials (ADC)

```bash
# Authenticate with GCP
gcloud auth application-default login

# Ingest GCP pricing data (EU regions only)
make ingest-gcp

# Compute machine type prices from unit prices
make ingest-gcp-machines

# Or do both in sequence
make ingest-gcp && make ingest-gcp-machines
```

**What it does**:
1. Fetches Compute Engine SKUs from GCP Billing Catalog API
2. Creates pseudo instance types for CPU/RAM unit prices
3. Computes prices for 92 machine types across 8 families:
   - E2 (12 types)
   - N1 (22 types)
   - N2 (29 types)
   - C2 (5 types)
   - C2D (7 types)
   - M1 (4 types)
   - M2 (3 types)
   - T2D (10 types)

### Hetzner Ingestion

**Prerequisites**: Hetzner Cloud API token

```bash
# Set token in .env
HETZNER_API_TOKEN=your-token-here

# Ingest Hetzner pricing data
make ingest-hetzner
```

### Ingest All Providers

```bash
make ingest-all
```

---

## 🧪 Testing

### Run All Tests

```bash
make test
```

### Run Tests with Coverage

```bash
make test-cov

# View HTML coverage report
open htmlcov/index.html
```

### Test Script

```bash
# Run API test script
./scripts/test_recommendations.sh
```

---

## 🔍 Example Workflows

### Workflow 1: Find Cheapest EU Instance

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-bypass" \
  -d '{
    "min_vcpu": 4,
    "min_ram_gb": 8,
    "arch": "x86_64",
    "region_constraint": "EU",
    "max_price_eur_per_hour": 0.5,
    "purchase_model": "on_demand",
    "limit": 5
  }' \
  http://localhost:8000/api/recommendations | python3 -m json.tool
```

### Workflow 2: Compare Spot vs On-Demand

```bash
# On-demand recommendations
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-bypass" \
  -d '{"min_vcpu": 2, "min_ram_gb": 4, "arch": "x86_64", "max_price_eur_per_hour": 1.0, "purchase_model": "on_demand", "limit": 3}' \
  http://localhost:8000/api/recommendations | python3 -m json.tool

# Spot recommendations
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-bypass" \
  -d '{"min_vcpu": 2, "min_ram_gb": 4, "arch": "x86_64", "max_price_eur_per_hour": 1.0, "purchase_model": "spot", "limit": 3}' \
  http://localhost:8000/api/recommendations | python3 -m json.tool
```

### Workflow 3: Explore Available Instance Types

```bash
# List all GCP instance types
curl -H "X-API-Key: dev-bypass" \
  "http://localhost:8000/api/instance-types?provider=gcp&limit=20" | \
  python3 -m json.tool | grep -E '"name"|"vcpu"|"ram_gb"|"family"'

# Find high-memory instances
curl -H "X-API-Key: dev-bypass" \
  "http://localhost:8000/api/instance-types?min_ram_gb=64&limit=10" | \
  python3 -m json.tool
```

---

## 🐛 Troubleshooting

### API Returns 401 Unauthorized

- Check if `API_KEY` is set in production mode
- In development, ensure `API_KEY` is not set (or use `dev-bypass`)
- Verify `X-API-Key` header is included in requests

### No Instance Types Returned

- Run ingestion: `make ingest-gcp` or `make ingest-hetzner`
- Check database has data: `curl -H "X-API-Key: dev-bypass" http://localhost:8000/api/providers`
- Verify migrations ran: `make migrate`

### Recommendations Return Empty

- Ensure you have pricing data ingested
- Check `max_price_eur_per_hour` is high enough
- Verify `region_constraint` matches available regions
- Check logs: `docker compose logs api`

---

## 📚 Additional Resources

- **API Documentation**: `http://localhost:8000/docs`
