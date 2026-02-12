# End-to-End Manual Testing Guide

This document provides step-by-step instructions to manually test CloudBroker from scratch: setup, data ingestion, and verifying every API endpoint.

## Overview

```
1. Start infrastructure          (PostgreSQL + API via Docker or local)
2. Run database migrations       (Alembic)
3. Ingest provider pricing data  (one or more providers)
4. Verify health endpoint
5. Query providers, regions, instance types, prices
6. Test recommendations API      (the core feature)
7. Test price analytics
8. Verify Prometheus metrics
9. Test authentication
10. Cleanup
```

---

## Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for local development)
- `curl` or `httpie` for API calls
- Provider credentials (see per-provider sections below)

---

## Step 1: Start Infrastructure

### Option A: Docker Compose (recommended)

```bash
cd cloudbroker

# Copy environment file
cp .env.example .env
# Edit .env if needed (defaults work for Docker)

# Start PostgreSQL + API
make up

# Verify containers are running
docker compose ps
```

### Option B: Local Development

```bash
cd cloudbroker

# Create virtualenv
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
make install

# Copy and configure environment
cp .env.example .env
# Edit .env: set DATABASE_URL to point to your PostgreSQL instance

# Start PostgreSQL only
docker compose up -d db

# Run API locally with hot-reload
make dev
```

**Expected**: API is accessible at `http://localhost:8000`.

---

## Step 2: Run Database Migrations

```bash
make migrate
```

**Expected output**: Alembic applies migrations (3 revisions).

**Verify**:
```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

**Expected**:
```json
{
  "status": "ok",
  "checks": {
    "database": {
      "status": "ok"
    }
  }
}
```

---

## Step 3: Ingest Provider Pricing Data

You can ingest data from one or more providers. Each provider has its own prerequisites. Pick the providers you have credentials for.

> **Note**: In development mode (default), API key authentication is bypassed. All `curl` examples below omit the `X-API-Key` header for brevity. In production, add `-H "X-API-Key: YOUR_KEY"` to every `/api/*` request.

### 3a. GCP (Google Cloud Platform)

**Prerequisites**:
- GCP service account JSON with Billing Catalog read access, OR
- Application Default Credentials (`gcloud auth application-default login`)

```bash
# Set credentials (pick one)
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/gcp-sa.json
# OR
gcloud auth application-default login

# Ingest GCP billing catalog (unit prices)
make ingest-gcp

# Compute machine type prices from unit prices
make ingest-gcp-machines
```

**Verify**:
```bash
curl -s "http://localhost:8000/api/providers" | python3 -m json.tool
```

**Expected**: GCP provider appears in the list with `"slug": "gcp"`.

```bash
curl -s "http://localhost:8000/api/instance-types?provider=gcp&limit=5" | python3 -m json.tool
```

**Expected**: GCP instance types (e2-standard-2, n1-standard-4, etc.) with vCPU and RAM values.

### 3b. Hetzner

**Prerequisites**:
- Hetzner Cloud API token (generate at https://console.hetzner.cloud)

```bash
export HETZNER_API_TOKEN=your-token-here
make ingest-hetzner
```

**Verify**:
```bash
curl -s "http://localhost:8000/api/instance-types?provider=hetzner&limit=5" | python3 -m json.tool
```

### 3c. AWS

**Prerequisites**:
- AWS credentials (`AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`), or AWS CLI profile configured

```bash
export AWS_ACCESS_KEY_ID=your-key
export AWS_SECRET_ACCESS_KEY=your-secret

# Quick ingestion (eu-west-1 only, ~1 min)
make ingest-aws-quick

# OR full ingestion (all EU regions, ~10 min)
make ingest-aws
```

**Verify**:
```bash
curl -s "http://localhost:8000/api/instance-types?provider=aws&limit=5" | python3 -m json.tool
```

### 3d. Azure

**Prerequisites**: None (uses public Retail Prices API).

```bash
# Quick ingestion (westeurope only)
make ingest-azure-quick

# OR full ingestion (all EU regions)
make ingest-azure

# Optional: Spot VM pricing
make ingest-azure-spot-quick
```

**Verify**:
```bash
curl -s "http://localhost:8000/api/instance-types?provider=azure&limit=5" | python3 -m json.tool
```

### 3e. Scaleway

**Prerequisites**:
- Scaleway API keys (generate at https://console.scaleway.com/iam/api-keys)

```bash
export SCW_ACCESS_KEY=your-access-key
export SCW_SECRET_KEY=your-secret-key
make ingest-scaleway
```

**Verify**:
```bash
curl -s "http://localhost:8000/api/instance-types?provider=scaleway&limit=5" | python3 -m json.tool
```

### 3f. DigitalOcean

**Prerequisites**:
- DigitalOcean API token (generate at https://cloud.digitalocean.com/account/api/tokens)

```bash
export DIGITALOCEAN_API_TOKEN=your-token
make ingest-digitalocean
```

### 3g. OVH

**Prerequisites**:
- OVH API keys (create at https://api.ovh.com/createToken/)

```bash
export OVH_APPLICATION_KEY=your-ak
export OVH_APPLICATION_SECRET=your-as
export OVH_CONSUMER_KEY=your-ck
export OVH_SERVICE_NAME=your-project-id
make ingest-ovh
```

### 3h. Aruba Cloud

**Prerequisites**:
- Aruba Cloud API credentials

```bash
export ARUBA_API_KEY=your-client-id
export ARUBA_API_SECRET=your-client-secret
export ARUBA_PROJECT_ID=your-project-id
make ingest-aruba
```

### 3i. Ingest All Providers

If you have all credentials configured:

```bash
make ingest-all
```

This runs all connectors sequentially and continues on errors.

---

## Step 4: Verify Providers

```bash
curl -s "http://localhost:8000/api/providers" | python3 -m json.tool
```

**Expected**: All ingested providers listed with `name`, `slug`, `website`, and `type` (hyperscaler/eu/regional).

---

## Step 5: Verify Regions

```bash
# List all regions
curl -s "http://localhost:8000/api/regions?limit=50" | python3 -m json.tool

# Filter by provider
curl -s "http://localhost:8000/api/regions?provider=gcp" | python3 -m json.tool

# Check EU regions
curl -s "http://localhost:8000/api/regions?provider=hetzner" | python3 -m json.tool
```

**Expected**: Regions have `slug`, `name`, `country`, and `is_eu` fields. EU regions should have `"is_eu": true`.

---

## Step 6: Verify Instance Types

```bash
# List all instance types
curl -s "http://localhost:8000/api/instance-types?limit=10" | python3 -m json.tool

# Filter by provider
curl -s "http://localhost:8000/api/instance-types?provider=gcp&limit=10" | python3 -m json.tool

# Filter by minimum specs
curl -s "http://localhost:8000/api/instance-types?min_vcpu=4&min_ram_gb=8&limit=10" | python3 -m json.tool

# Filter by architecture
curl -s "http://localhost:8000/api/instance-types?arch=x86_64&limit=10" | python3 -m json.tool
```

**Expected**: Instance types have `name`, `family`, `vcpu`, `ram_gb`, and `arch` fields.

---

## Step 7: Verify Prices

```bash
# List all prices
curl -s "http://localhost:8000/api/prices?limit=10" | python3 -m json.tool

# Filter by provider and region
curl -s "http://localhost:8000/api/prices?provider=gcp&region=europe-west1&limit=10" | python3 -m json.tool

# Filter by specs
curl -s "http://localhost:8000/api/prices?min_vcpu=2&min_ram_gb=4&limit=10" | python3 -m json.tool
```

**Expected**: Prices have `price_per_hour`, `currency`, `purchase_model`, and `timestamp` fields.

---

## Step 8: Test Recommendations API

This is the core feature. The recommendations API finds the best instances matching your requirements.

### 8a. Basic Recommendation (EU, On-Demand)

```bash
curl -s -X POST http://localhost:8000/api/recommendations \
  -H "Content-Type: application/json" \
  -d '{
    "min_vcpu": 2,
    "min_ram_gb": 4,
    "arch": "x86_64",
    "region_constraint": "EU",
    "max_price_eur_per_hour": 0.2,
    "limit": 5
  }' | python3 -m json.tool
```

**Expected**: A `recommendations` array sorted by score (highest first). Each recommendation includes:
- `instance_type_name`, `provider_slug`, `region_slug`
- `vcpu`, `ram_gb`, `arch`
- `score` (0-1, higher is better)
- `explain` object with scoring breakdown

### 8b. Spot Instance Recommendations

```bash
curl -s -X POST http://localhost:8000/api/recommendations \
  -H "Content-Type: application/json" \
  -d '{
    "min_vcpu": 2,
    "min_ram_gb": 4,
    "arch": "x86_64",
    "region_constraint": "EU",
    "max_price_eur_per_hour": 0.1,
    "purchase_model": "spot",
    "limit": 5
  }' | python3 -m json.tool
```

**Expected**: Spot recommendations include `spot_availability_score` and `interruption_risk_score` in the `explain` object.

### 8c. Provider Filtering

```bash
# Only GCP and Hetzner
curl -s -X POST http://localhost:8000/api/recommendations \
  -H "Content-Type: application/json" \
  -d '{
    "min_vcpu": 2,
    "min_ram_gb": 4,
    "arch": "x86_64",
    "max_price_eur_per_hour": 0.5,
    "allowed_providers": ["gcp", "hetzner"],
    "limit": 10
  }' | python3 -m json.tool
```

**Expected**: Only recommendations from GCP and Hetzner.

```bash
# Exclude AWS
curl -s -X POST http://localhost:8000/api/recommendations \
  -H "Content-Type: application/json" \
  -d '{
    "min_vcpu": 2,
    "min_ram_gb": 4,
    "arch": "x86_64",
    "max_price_eur_per_hour": 0.5,
    "excluded_providers": ["aws"],
    "limit": 10
  }' | python3 -m json.tool
```

**Expected**: No AWS results.

### 8d. Preferred Providers (Score Boost)

```bash
curl -s -X POST http://localhost:8000/api/recommendations \
  -H "Content-Type: application/json" \
  -d '{
    "min_vcpu": 2,
    "min_ram_gb": 4,
    "arch": "x86_64",
    "region_constraint": "EU",
    "max_price_eur_per_hour": 0.5,
    "preferred_providers": ["hetzner"],
    "limit": 10
  }' | python3 -m json.tool
```

**Expected**: Hetzner recommendations appear higher in the list (1.2x score multiplier applied).

### 8e. Multi-Criteria Scoring

```bash
# Cost-optimized (prioritize cheapest)
curl -s -X POST http://localhost:8000/api/recommendations \
  -H "Content-Type: application/json" \
  -d '{
    "min_vcpu": 4,
    "min_ram_gb": 8,
    "arch": "x86_64",
    "max_price_eur_per_hour": 1.0,
    "cost_weight": 0.8,
    "performance_weight": 0.1,
    "reliability_weight": 0.1,
    "limit": 5
  }' | python3 -m json.tool

# Performance-optimized
curl -s -X POST http://localhost:8000/api/recommendations \
  -H "Content-Type: application/json" \
  -d '{
    "min_vcpu": 4,
    "min_ram_gb": 8,
    "arch": "x86_64",
    "max_price_eur_per_hour": 1.0,
    "cost_weight": 0.1,
    "performance_weight": 0.8,
    "reliability_weight": 0.1,
    "limit": 5
  }' | python3 -m json.tool
```

**Expected**: Different ranking order depending on weights. Cost-optimized should show cheapest first; performance-optimized should favor larger instance families.

### 8f. ARM64 Architecture

```bash
curl -s -X POST http://localhost:8000/api/recommendations \
  -H "Content-Type: application/json" \
  -d '{
    "min_vcpu": 2,
    "min_ram_gb": 4,
    "arch": "arm64",
    "max_price_eur_per_hour": 0.5,
    "limit": 5
  }' | python3 -m json.tool
```

**Expected**: Only ARM64 instance types (e.g., AWS Graviton, Hetzner CAX).

### 8g. Edge Cases

```bash
# Very low budget (may return empty)
curl -s -X POST http://localhost:8000/api/recommendations \
  -H "Content-Type: application/json" \
  -d '{
    "min_vcpu": 16,
    "min_ram_gb": 64,
    "arch": "x86_64",
    "max_price_eur_per_hour": 0.01,
    "limit": 5
  }' | python3 -m json.tool
```

**Expected**: Empty `recommendations` array (no instances that cheap).

```bash
# Invalid request (missing required fields)
curl -s -X POST http://localhost:8000/api/recommendations \
  -H "Content-Type: application/json" \
  -d '{"min_vcpu": 2}' | python3 -m json.tool
```

**Expected**: `422 Unprocessable Entity` with validation errors.

---

## Step 9: Test Price Analytics

### Price Changes (recent)

```bash
curl -s "http://localhost:8000/api/price-analytics/changes?since_hours=24" | python3 -m json.tool
```

**Expected**: `changes` array showing instances whose price changed in the last 24 hours. May be empty if only one ingestion has been run.

> **Tip**: To test price changes, ingest the same provider twice (prices may update). Or run `make ingest-azure-quick` twice with some time gap.

### Price Trends

```bash
curl -s "http://localhost:8000/api/price-analytics/trends?days=30" | python3 -m json.tool
```

**Expected**: `trends` array with `trend_direction` (increasing/decreasing/stable), price change percentages, and data point counts.

---

## Step 10: Verify Prometheus Metrics

```bash
curl -s http://localhost:8000/metrics | head -30
```

**Expected**: Prometheus text format output with:
- `http_requests_total{method="...", endpoint="...", status_code="..."}`
- `http_request_duration_seconds_bucket{...}`
- `db_queries_total{operation="..."}`
- `ingestion_runs_total{provider="...", status="..."}`

After running several API calls, the counters should reflect the requests made.

---

## Step 11: Test Authentication

### Development Mode (default)

In development mode with no `API_KEY` set, all API endpoints work without authentication:

```bash
# Should succeed (no auth header)
curl -s "http://localhost:8000/api/providers" | python3 -m json.tool
```

### Production Mode

To test production authentication:

```bash
# Generate an API key
API_KEY=$(openssl rand -hex 32)
echo "API_KEY=$API_KEY"

# Restart the API with the key set
# Option 1: Add to .env and restart
echo "API_KEY=$API_KEY" >> .env
docker compose restart api

# Option 2: Or set inline
API_KEY=$API_KEY make dev
```

Test authentication:

```bash
# Should return 401 (no key)
curl -s "http://localhost:8000/api/providers"
# Expected: {"detail": "Missing API key. Provide X-API-Key header."}

# Should return 403 (wrong key)
curl -s -H "X-API-Key: wrong-key" "http://localhost:8000/api/providers"
# Expected: {"detail": "Invalid API key"}

# Should succeed (correct key)
curl -s -H "X-API-Key: $API_KEY" "http://localhost:8000/api/providers" | python3 -m json.tool
```

### Health and Metrics (no auth required)

```bash
# These should always work, even in production mode
curl -s http://localhost:8000/health
curl -s http://localhost:8000/metrics | head -5
```

---

## Step 12: Run Automated Test Script

```bash
./scripts/test_recommendations.sh
```

This script runs a set of recommendation queries and reports which providers returned results.

---

## Step 13: Run Unit Tests

```bash
make test
```

**Expected**: All tests pass (24 tests). One test (`test_post_recommendations_preferred_providers`) may fail if the database has real ingested data that changes score ordering -- this is a known data-dependent test.

With coverage:

```bash
make test-cov
# Open coverage report
open htmlcov/index.html
```

---

## Cleanup

```bash
# Stop all services
make down

# Remove Docker volumes (deletes all data)
docker compose down -v

# Remove Python artifacts
make clean
```

---

## Quick Reference

| What | Command |
|------|---------|
| Start stack | `make up` |
| Stop stack | `make down` |
| Run migrations | `make migrate` |
| Ingest all providers | `make ingest-all` |
| Ingest GCP only | `make ingest-gcp && make ingest-gcp-machines` |
| Ingest Azure (quick) | `make ingest-azure-quick` |
| Health check | `curl http://localhost:8000/health` |
| List providers | `curl http://localhost:8000/api/providers` |
| Get recommendations | `curl -X POST http://localhost:8000/api/recommendations -H "Content-Type: application/json" -d '{"min_vcpu":2,"min_ram_gb":4,"arch":"x86_64","max_price_eur_per_hour":0.2}'` |
| Swagger UI | http://localhost:8000/docs |
| Metrics | `curl http://localhost:8000/metrics` |
| Run tests | `make test` |
| All make targets | `make help` |

---

## Troubleshooting

### API returns 500 / database errors

- Check PostgreSQL is running: `docker compose ps`
- Check migrations ran: `make migrate`
- Check database URL in `.env` matches the running PostgreSQL

### No recommendations returned

- Verify data was ingested: `curl http://localhost:8000/api/providers`
- Verify prices exist: `curl "http://localhost:8000/api/prices?limit=5"`
- Try a higher `max_price_eur_per_hour` (e.g., 1.0)
- Try `region_constraint: "ANY"` instead of `"EU"`
- Check the architecture matches available data (`x86_64` vs `arm64`)

### Ingestion fails

- Check required environment variables are set (see per-provider sections)
- Check network connectivity to provider APIs
- Check logs: `docker compose logs -f api`

### Rate limit exceeded (429)

- Default: 100 requests/minute
- Wait 60 seconds, or increase `RATE_LIMIT_PER_MINUTE` in `.env`
