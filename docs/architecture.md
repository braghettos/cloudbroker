# Architecture

## Overview

CloudBroker is a compute pricing marketplace API. It ingests instance pricing data from multiple cloud providers, normalizes it into a common schema, and exposes a recommendation engine that returns the cheapest instances matching a set of constraints (CPU, RAM, architecture, region, price ceiling).

It is the pricing backend for [Cloudburst Autoscaler](https://github.com/braghettos/cloudburst-autoscaler), which calls `POST /api/recommendations` to decide which cloud VM to provision when Kubernetes pods are unschedulable.

```
               ┌──────────────────────────────────────────────────┐
               │                  CloudBroker                     │
               │                                                  │
  Ingestion    │   ┌──────────┐    ┌───────────┐    ┌─────────┐  │   API
  (CLI)        │   │Connectors│───>│ Ingestion │───>│   DB    │  │   (FastAPI)
  ─────────────┤   │ GCP,AWS, │    │  Service  │    │ Postgres│  ├──────────────
  make ingest-*│   │ Azure,.. │    │ (upsert)  │    │         │  │  /api/recommendations
               │   └──────────┘    └───────────┘    └────┬────┘  │  /api/providers
               │                                         │       │  /api/regions
               │                    ┌───────────┐        │       │  /api/instance-types
               │                    │Recommend- │<───────┘       │  /api/prices
               │                    │ation      │                │  /api/price-analytics
               │                    │ Engine    │                │
               │                    └───────────┘                │
               └──────────────────────────────────────────────────┘
```

## Data Model

Five core entities form a hierarchy from provider down to individual price records:

```
Provider ──1:N──> Region
    │                │
    └──1:N──> InstanceType ──1:N──> Price <──N:1── Region
                                      │
                                   FxRate (lookup)
```

### Provider

Represents a cloud provider. Each has a slug (e.g. `aws`, `hetzner`, `scaleway`) and a type that classifies its scale:

| Type | Examples | Reliability Score |
|------|----------|-------------------|
| `HYPERSCALER` | AWS, Azure, GCP | 0.95 |
| `EU` | Scaleway, OVH | 0.85 |
| `REGIONAL` | Hetzner, DigitalOcean | 0.75 |

### Region

A data center location belonging to a provider. Key fields:

- `slug`: Provider-specific identifier (e.g. `eu-west-1`, `fsn1`, `fr-par-1`)
- `country`: ISO 2-letter code
- `is_eu`: Whether the region is in the EU (used by the `EU` region constraint)

Unique constraint: `(provider_id, slug)`.

### InstanceType

A VM size offered by a provider. Key fields:

- `name`: Provider-specific name (e.g. `n1-standard-4`, `cx23`, `DEV1-S`)
- `vcpu`, `ram_gb`: Compute specs
- `arch`: `x86_64` or `arm64`
- `family`: Instance family (e.g. `N1`, `C2`, `CAX11`)
- `metadata_json`: Optional JSON for provider-specific details

Unique constraint: `(provider_id, name)`.

### Price

A point-in-time hourly price for an instance type in a region. Key fields:

- `purchase_model`: `ON_DEMAND`, `SPOT`, or `RESERVED`
- `currency`: Original currency (e.g. `USD`, `EUR`)
- `price_per_hour`: Price in original currency
- `price_eur_per_hour`: EUR-normalized price (nullable; filled via FX conversion)
- `timestamp`: When the price was recorded (truncated to hour boundary)

Prices are **append-only**. Duplicates within the same hour are skipped. The recommendation engine always uses the latest price per `(instance_type, region)` pair.

### FxRate

Exchange rate lookup table for currency normalization:

- `from_currency` / `to_currency`: e.g. `USD` -> `EUR`
- `rate`: Conversion factor
- `effective_date`: When the rate applies

A stub rate of `0.92` for USD->EUR is seeded automatically if no rate exists.

## Recommendation Engine

The core value of CloudBroker. Given resource constraints, it returns a ranked list of instance types scored by price efficiency and fit.

### Request Flow

```
Client Request                  SQL Query                     Scoring
─────────────                  ─────────                     ───────
min_vcpu: 2          ──>   WHERE vcpu >= 2            ──>   For each candidate:
min_ram_gb: 4              AND ram_gb >= 4.0                 1. normalized_price
arch: x86_64               AND arch = 'x86_64'              2. resource_fit
region: EU                 AND is_eu = true                  3. Apply weights
max_price: 0.50            AND eff_price <= 0.50             4. Apply boosts/penalties
allowed: [gcp,hetzner]    AND provider IN (...)              5. Sort by score DESC
```

### Candidate Filtering

1. **Spec filter**: `vcpu >= min_vcpu`, `ram_gb >= min_ram_gb`, `arch = arch`
2. **Price filter**: `COALESCE(price_eur_per_hour, price_per_hour) <= max_price`
3. **Purchase model**: `ON_DEMAND`, `SPOT`, or `RESERVED`
4. **Region constraint**: When `EU`, adds `WHERE is_eu = true`
5. **Provider filter**: `allowed_providers` (whitelist) and `excluded_providers` (blacklist)
6. **Latest price**: Subquery selects `MAX(timestamp)` per `(instance_type_id, region_id)` to use only the most recent price

Architecture values are normalized: `amd64` -> `x86_64`, `aarch64` -> `arm64`.

### Scoring

Two scoring modes are available. The default is **legacy mode** (price + fit):

#### Legacy Mode (default)

```
score = (price_weight × normalized_price) + (fit_weight × resource_fit)
```

| Component | Formula | Range |
|-----------|---------|-------|
| `normalized_price` | `(max_price - candidate_price) / price_range` | 0-1 (cheaper = higher) |
| `resource_fit` | `min(1, requested_vcpu/actual_vcpu) × min(1, requested_ram/actual_ram)` | 0-1 (closer match = higher) |

Default weights: `price_weight=0.5`, `fit_weight=0.5`. Clients can override these per request.

#### Multi-Criteria Mode

Activated when `cost_weight`, `performance_weight`, or `reliability_weight` are provided:

```
score = (cost_weight × cost_score) + (perf_weight × perf_score) + (rel_weight × rel_score)
```

| Component | How it's computed |
|-----------|-------------------|
| Cost score | Same as `normalized_price` |
| Performance score | `0.6 × vcpu_score + 0.4 × ram_score` (+5% boost for C-series families) |
| Reliability score | Based on provider type: hyperscaler=0.95, EU=0.85, regional=0.75 |

Default weights: `cost=0.4`, `performance=0.3`, `reliability=0.3`. Weights are normalized to sum to 1.0.

### Score Adjustments

After the base score is computed, several adjustments are applied:

| Adjustment | Condition | Effect |
|------------|-----------|--------|
| **Preferred providers** | Provider slug in `preferred_providers` list | `score × 1.2` |
| **USD-only penalty** | Price currency is not EUR | `score × 0.95` |
| **Spot availability** | `purchase_model=spot` | Bonus based on instance size and provider type |
| **Interruption risk** | `purchase_model=spot` | `score × (1.0 - risk × 0.3)` penalty |

### Response

Each recommendation includes an `explain` block with all scoring components, making the ranking transparent:

```json
{
  "instance_type_name": "cx23",
  "provider_slug": "hetzner",
  "region_slug": "fsn1",
  "score": 0.9898,
  "explain": {
    "resource_fit": 1.0,
    "normalized_price": 0.9797,
    "price_weight": 0.5,
    "fit_weight": 0.5,
    "price_eur_per_hour": 0.0048,
    "region_is_eu": true
  }
}
```

## Ingestion Pipeline

Data flows from provider APIs into the database through a CLI → connector → ingestion service chain.

### Architecture

```
make ingest-hetzner
        │
        v
apps/ingestor/cli.py          (CLI entry point, Click)
        │
        ├── ensure_stub_fx_rates()     (seed USD→EUR if missing)
        ├── ensure_provider()          (upsert provider row)
        │
        v
connectors/hetzner.py         (provider-specific API client)
        │
        ├── fetch locations       → ensure_region() per location
        ├── fetch server_types    → ensure_instance_type() per type
        └── fetch prices          → to_eur() + append_price() per price
                                           │
                                           v
                                 app/services/ingestion.py  (upsert logic)
```

### CLI

```bash
python -m apps.ingestor ingest --provider <name> [--only-eu] [--quick] [--region <slug>]
```

Supported providers: `gcp`, `hetzner`, `aws`, `azure`, `digitalocean`, `scaleway`, `ovh`, `all`.

### Idempotency

All write operations are upserts:

| Operation | Key | Behavior |
|-----------|-----|----------|
| `ensure_provider` | `slug` | Update if exists, insert if not |
| `ensure_region` | `(provider_id, slug)` | Update if exists, insert if not |
| `ensure_instance_type` | `(provider_id, name)` | Update if exists, insert if not |
| `append_price` | `(instance_type_id, region_id, purchase_model, hour)` | Skip if duplicate in same hour, insert otherwise |

Ingestion is safe to re-run at any time.

### FX Conversion

All prices are stored with their original currency. EUR normalization happens at ingestion time:

1. Connector calls `get_rate_to_eur(currency, date)` to get the conversion rate
2. If `currency == "EUR"`, rate is `1.0`
3. Otherwise, looks up `fx_rates` table; falls back to `0.92` for USD→EUR
4. `price_eur_per_hour = round(price_per_hour × rate, 6)`

### Supported Providers

| Provider | API | Auth Required | Native Currency |
|----------|-----|---------------|-----------------|
| Hetzner | Hetzner Cloud API | API token | EUR |
| GCP | Cloud Billing Catalog | ADC / service account | USD |
| AWS | Pricing API + EC2 Spot API | IAM credentials | USD |
| Azure | Retail Prices API | None (public) | USD |
| DigitalOcean | DO API | API token | USD |
| Scaleway | Scaleway API | Access key + secret | EUR |
| OVH | OVH API | App key + secret | EUR |

## Price Analytics

Two analytical endpoints operate on the stored price history:

### Change Detection (`GET /api/price-analytics/changes`)

Compares the two most recent prices for each `(instance_type, region, purchase_model)` triple:

- Calculates absolute and percentage change
- Skips changes below `0.0001 EUR` tolerance
- Filterable by `purchase_model` and `since_hours`

### Trend Analysis (`GET /api/price-analytics/trends`)

Analyzes price direction over a configurable window (default: 30 days):

- Compares first and latest price in the window
- Classifies as `increasing`, `decreasing`, or `stable` (threshold: 0.1%)
- Reports data point count for confidence assessment

## API Surface

All endpoints except `/health` and `/api/metrics` require API key authentication (`X-API-Key` header). In development mode (`APP_ENV=development` without `API_KEY` set), auth is bypassed.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check (includes DB connectivity) |
| `GET` | `/api/providers` | List providers (paginated) |
| `GET` | `/api/regions` | List regions (filterable by provider) |
| `GET` | `/api/instance-types` | List instance types (filterable) |
| `GET` | `/api/prices` | List prices (filterable) |
| `POST` | `/api/recommendations` | Get ranked instance recommendations |
| `GET` | `/api/price-analytics/changes` | Detect price changes |
| `GET` | `/api/price-analytics/trends` | Analyze price trends |
| `GET` | `/api/metrics` | Prometheus metrics |

## Integration with Cloudburst

Cloudburst Autoscaler's `internal/cloudbroker/client.go` calls `POST /api/recommendations` with:

```json
{
  "min_vcpu": 2,
  "min_ram_gb": 4,
  "arch": "x86_64",
  "region_constraint": "EU",
  "max_price_eur_per_hour": 0.15,
  "allowed_providers": ["gcp", "scaleway"]
}
```

CloudBroker returns ranked results. Cloudburst picks the top recommendation and uses the `provider_slug`, `region_slug`, and `instance_type_name` to provision a VM via the appropriate cloud provider.

The `allowed_providers` field is critical -- it maps directly to the NodePool's `spec.requirements.allowedProviders`, ensuring Cloudburst only provisions VMs from providers it has credentials and NodeClasses configured for.

## Technology Stack

| Layer | Technology |
|-------|-----------|
| API framework | FastAPI |
| ORM | SQLAlchemy 2.x (async via asyncpg) |
| Database | PostgreSQL 16 |
| Migrations | Alembic |
| Validation | Pydantic v2 |
| Rate limiting | SlowAPI |
| Metrics | prometheus-client |
| Logging | structlog |
| Containerization | Docker + docker-compose |
| CI | GitHub Actions (lint, test, Docker build + push to GHCR) |
