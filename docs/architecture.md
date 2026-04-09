# CloudBroker Architecture

This document describes the architecture of CloudBroker, a multi-cloud instance recommendation and price comparison engine built with FastAPI, SQLAlchemy, and PostgreSQL.

---

## Project Structure

```
app/                          # FastAPI application
  api/                        # Routers
    recommendations.py        # POST /api/recommendations
    providers.py              # GET /api/providers
    regions.py                # GET /api/regions
    instance_types.py         # GET /api/instance-types
    prices.py                 # GET /api/prices
    price_analytics.py        # GET /api/price-analytics/{changes,trends}
    health.py                 # GET /health
    metrics.py                # GET /api/metrics
    _pagination.py            # PaginatedQuery builder (limit/offset)
    utils.py                  # Shared API utilities
  core/                       # Cross-cutting infrastructure
    config.py                 # Pydantic Settings with secrets_dir support
    database.py               # AsyncSession factory (asyncpg)
    security.py               # API key verification
    logging.py                # Structured logging setup
    fx.py                     # FX rate lookup (USD -> EUR conversion)
    middleware.py              # Request timing, CORS, rate limiting
    constants.py              # Provider lists, carbon-publishing providers
    exceptions.py             # Custom exception handlers (validation, DB errors)
    metrics.py                # Prometheus-style metrics collection
  models/                     # SQLAlchemy ORM models
    provider.py               # Provider + ProviderType enum (HYPERSCALER, EU, REGIONAL)
    region.py                 # Region (slug, display_name, country_code, is_eu, lat/lon)
    instance_type.py          # InstanceType (vcpu, ram_gb, family, arch, gpu_count, gpu_model, etc.)
    price.py                  # Price (time-series, PurchaseModel enum: on_demand/spot/reserved)
    fx_rate.py                # FxRate (currency pair rates)
    egress_rate.py            # EgressRate (egress_eur_per_gb, cross_region_eur_per_gb)
    storage_rate.py           # StorageRate (eur_per_gb_month by storage tier)
    public_ip_rate.py         # PublicIpRate (eur_per_hour)
    os_license_rate.py        # OsLicenseRate (eur_per_vcpu_hour by OS)
    base.py                   # Declarative Base
  schemas/                    # Pydantic v2 request/response models
  services/                   # Business logic
    recommendation_service.py # Orchestrator: fetch -> score -> rank -> respond
    scoring.py                # 6 scoring functions (see Recommendation Scoring)
    tco.py                    # TCO computation + rate fetching from DB
    candidate_fetcher.py      # SQL query builder (15+ filters) + sustainability enrichment
    ingestion.py              # Shared upsert helpers for all connectors
    sustainability_service.py # Real-time carbon intensity (ENTSO-E, EIA APIs)
    price_analytics.py        # Price change detection and trend analysis
connectors/                   # Provider-specific ingestion modules
  base.py                     # Abstract BaseConnector interface
  _config_connector.py        # BaseConfigConnector for YAML-driven providers
  _config_loader.py           # Shared YAML loader for config-based connectors
  _cloud_clients.py           # Centralized boto3 / GCP client factories
  _egress_helper.py           # Shared egress rate ingestion
  _storage_helper.py          # Shared storage rate ingestion
  _http.py                    # Shared HTTP client utilities
  _verify_base.py             # Shared verification infrastructure
  aws.py                      # AWS connector (bulk JSON pricing)
  azure.py                    # Azure connector (Retail Prices API)
  gcp.py                      # GCP connector (Compute Engine API)
  hetzner.py                  # Hetzner connector (authenticated API)
  digitalocean.py             # DigitalOcean connector (authenticated API)
  ovh.py                      # OVH connector (public catalog)
  scaleway.py                 # Scaleway connector (public catalog)
  upcloud.py                  # UpCloud connector (authenticated API)
  seeweb.py                   # Seeweb connector (authenticated API)
  ibm_cloud.py                # IBM Cloud connector (authenticated API)
  contabo.py                  # Contabo connector (config-based)
  gcore.py                    # Gcore connector (config-based)
  cherry_servers.py           # Cherry Servers connector (config-based)
  clouding.py                 # Clouding connector (config-based)
  exoscale.py                 # Exoscale connector (config-based)
  aruba.py                    # Aruba Cloud connector (config-based)
  egress_aws.py               # AWS egress rate ingestion
  egress_azure.py             # Azure egress rate ingestion
  egress_gcp.py               # GCP egress rate ingestion
  egress_config.py            # Config-based egress rate ingestion
  storage_aws.py              # AWS storage rate ingestion
  storage_azure.py            # Azure storage rate ingestion
  storage_gcp.py              # GCP storage rate ingestion
  storage_config.py           # Config-based storage rate ingestion
  sustainability.py           # Sustainability data connector
  os_license_config.py        # OS license rate loader
  public_ip_config.py         # Public IP rate loader
  *_verify.py                 # Per-provider ingestion verification modules
  *.yaml                      # Rate configuration files
apps/ingestor/                # CLI entry point (python -m apps.ingestor ingest --provider ...)
helm/cloudbroker/             # Helm chart for Kubernetes deployment
migrations/                   # Alembic migrations (8 versions)
  versions/
    001_compute_pricing_marketplace.py
    002_fx_rates_and_eur_metadata.py
    003_convert_provider_type_enum_to_lowercase.py
    004_add_egress_rates.py
    005_add_cost_components.py
    006_add_sustainability.py
    007_add_price_composite_index.py
    008_enhance_instance_type_and_price_schema.py
tests/                        # Test suite
config/                       # External configuration files
scripts/                      # Utility scripts
docker/                       # Dockerfiles
docker-compose.yml            # Local development stack
Makefile                      # Task runner (ingest, verify, test, deploy)
```

---

## Data Flow

### Ingestion Pipeline

```
CLI (python -m apps.ingestor)
  |
  v
Connector (aws.py, azure.py, contabo.py, ...)
  |-- Fetches pricing data from provider API or config YAML
  |-- Normalizes: family, arch, cpu_type, gpu_count, is_current_gen
  |
  v
ingestion.py (shared upsert helpers)
  |-- Upserts Provider, Region, InstanceType rows
  |-- Inserts Price rows (time-series append)
  |-- Upserts EgressRate, StorageRate, PublicIpRate, OsLicenseRate
  |
  v
PostgreSQL
```

### API Request Flow

```
HTTP Request
  |
  v
FastAPI Router (app/api/recommendations.py)
  |
  v
RecommendationService.get_recommendations()           # Orchestrator
  |
  |-- fetch_candidates(db, req)                        # candidate_fetcher.py
  |     Builds SQL query with 15+ filter conditions
  |     Returns list[Candidate] dataclass instances
  |
  |-- enrich_and_filter_sustainability(db, candidates)  # candidate_fetcher.py
  |     Fetches real-time carbon intensity via sustainability_service.py
  |     Filters by min_renewable_pct / max_carbon_intensity
  |
  |-- compute_tco(db, candidates, req)                 # tco.py
  |     Fetches egress, storage, public IP, OS license rates
  |     Computes total hourly cost per candidate
  |
  |-- _score_candidates(candidates, ...)               # recommendation_service.py + scoring.py
  |     Applies scoring functions (reliability, performance, spot, sustainability)
  |     Combines with configurable weights
  |
  v
JSON Response: ranked list of RecommendationItem objects
```

---

## Connector Types

Connectors live in `connectors/` and fall into three categories:

### 1. Public API (no authentication required)

| Provider | File | Data Source |
|----------|------|-------------|
| Azure | `connectors/azure.py` | Azure Retail Prices REST API |
| AWS | `connectors/aws.py` | Bulk JSON pricing files |
| OVH | `connectors/ovh.py` | OVH public catalog API |
| Scaleway | `connectors/scaleway.py` | Scaleway public catalog API |

### 2. Authenticated API

| Provider | File | Auth Method |
|----------|------|-------------|
| Hetzner | `connectors/hetzner.py` | API token |
| DigitalOcean | `connectors/digitalocean.py` | API token |
| UpCloud | `connectors/upcloud.py` | Username + password |
| Seeweb | `connectors/seeweb.py` | API token |
| GCP | `connectors/gcp.py` | Service account JSON |
| IBM Cloud | `connectors/ibm_cloud.py` | API key |

### 3. Config-based (BaseConfigConnector + YAML)

These providers use `connectors/_config_connector.py` which reads instance definitions from YAML configuration files via `connectors/_config_loader.py`. No live API call is needed -- pricing is maintained in config.

| Provider | File |
|----------|------|
| Contabo | `connectors/contabo.py` |
| Gcore | `connectors/gcore.py` |
| Cherry Servers | `connectors/cherry_servers.py` |
| Clouding | `connectors/clouding.py` |
| Exoscale | `connectors/exoscale.py` |
| Aruba Cloud | `connectors/aruba.py` |

### Verification

Each connector that supports verification has a corresponding `*_verify.py` module (e.g., `connectors/aws_verify.py`, `connectors/hetzner_verify.py`). These compare ingested data in the database against the live provider API to detect drift. The shared infrastructure lives in `connectors/_verify_base.py`.

---

## Secrets Management

Defined in `app/core/config.py` using Pydantic `BaseSettings`:

```python
_SECRETS_DIR = os.environ.get("SECRETS_DIR", "/run/secrets/cloudbroker")

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        secrets_dir=_SECRETS_DIR if Path(_SECRETS_DIR).is_dir() else None,
    )
```

**Priority order** (highest to lowest):
1. Environment variables
2. Secret files at `SECRETS_DIR` (default: `/run/secrets/cloudbroker`)
3. `.env` file
4. Defaults in the Settings class

**Kubernetes deployment**: Mount a Kubernetes `Secret` as a volume at the `SECRETS_DIR` path. Each key in the Secret becomes a file that Pydantic reads automatically (e.g., `/run/secrets/cloudbroker/hetzner_api_token`).

**Local development**: A `.env` file in the project root works unchanged. No secrets directory is needed.

---

## Database Schema

PostgreSQL with async access via `asyncpg`. Schema managed by Alembic (8 migration versions in `migrations/versions/`).

### Core Tables

| Table | Model File | Purpose |
|-------|-----------|---------|
| `providers` | `app/models/provider.py` | Cloud provider registry with `ProviderType` enum (HYPERSCALER, EU, REGIONAL) |
| `regions` | `app/models/region.py` | Provider regions with country_code, is_eu flag, latitude/longitude |
| `instance_types` | `app/models/instance_type.py` | VM specs: vcpu, ram_gb, family, arch, cpu_type, gpu_count, gpu_model, is_current_gen |
| `prices` | `app/models/price.py` | Time-series pricing with `PurchaseModel` enum (on_demand, spot, reserved), currency, EUR equivalent |

### Rate Tables (TCO components)

| Table | Model File | Purpose |
|-------|-----------|---------|
| `fx_rates` | `app/models/fx_rate.py` | Currency exchange rates for EUR conversion |
| `egress_rates` | `app/models/egress_rate.py` | Network egress pricing (per GB, cross-region) |
| `storage_rates` | `app/models/storage_rate.py` | Block storage pricing (per GB/month by tier) |
| `public_ip_rates` | `app/models/public_ip_rate.py` | Public IPv4 address pricing (per hour) |
| `os_license_rates` | `app/models/os_license_rate.py` | OS license surcharges (per vCPU/hour, e.g., Windows, RHEL) |

### Key Relationships

- `Provider` 1:N `Region`
- `Provider` 1:N `InstanceType`
- `InstanceType` + `Region` -> `Price` (composite: instance_type_id, region_id, purchase_model, timestamp)
- Rate tables are keyed by provider slug (and optionally region slug)

---

## Normalization

All connectors normalize instance metadata to a common schema before ingestion.

### Family

Instances are classified into one of:
- `general-purpose`
- `compute-optimized`
- `memory-optimized`
- `storage-optimized`
- `gpu`

### Architecture

- `x86_64` (default for most providers)
- `arm64` (detected per provider, e.g., AWS Graviton, Azure Ampere, GCP Tau T2A)

### CPU Type

- `shared` -- burstable / shared-core instances
- `dedicated` -- dedicated vCPU instances

### GPU Detection

Lookup tables map provider-specific instance families or SKU patterns to GPU metadata:
- **AWS**: 17 GPU instance families mapped to GPU model and count (e.g., p5 -> NVIDIA H100, g5 -> NVIDIA A10G)
- **Azure**: 17 GPU SKU patterns mapped similarly (e.g., NC series -> NVIDIA T4/A100/H100)

### Current Generation

The `is_current_gen` flag is set by each connector to distinguish current-generation instances from legacy/deprecated ones. This allows the recommendation engine to prefer modern hardware.

---

## Recommendation Scoring

Implemented across `app/services/recommendation_service.py` and `app/services/scoring.py`.

### Scoring Functions (scoring.py)

Six standalone scoring functions, each returning a float in [0, 1]:

1. **`compute_reliability_score`** -- Based on `ProviderType`: HYPERSCALER=0.95, EU=0.85, REGIONAL=0.75
2. **`compute_performance_score`** -- Normalized vCPU (60%) + RAM (40%) relative to candidate set, with compute-family boost
3. **`compute_spot_availability_score`** -- Heuristic combining instance size, price position, and provider type
4. **`compute_interruption_risk_score`** -- Inverse of availability: smaller/cheaper instances = higher risk
5. **`compute_sustainability_score`** -- Carbon intensity (60% weight) + renewable energy percentage (40% weight), both normalized against candidate set. Returns None when no data is available
6. **USD_ONLY_PENALTY** -- 0.95 multiplier when only USD pricing exists (no native EUR)

### Scoring Modes

**Legacy mode** (default):
- `price_weight` (default 0.5) + `fit_weight` (default 0.5)
- Activated when the caller does not provide multi-criteria weights

**Multi-criteria mode**:
- `cost_weight` (default 0.4) + `performance_weight` (default 0.3) + `reliability_weight` (default 0.3)
- Activated when caller provides any of `cost_weight`, `performance_weight`, or `reliability_weight`
- Weights are normalized to sum to 1.0

**Sustainability boost**: When sustainability data is available, a bonus of up to `sustainability_boost_max` (default 0.15) is added to the final score.

### TCO (Total Cost of Ownership)

Implemented in `app/services/tco.py`. Computes five additional cost components beyond the base instance price:

1. **Egress cost** -- Based on data source location: same region = free, cross-region = cross_region rate, cross-provider = egress rate
2. **Storage cost** -- Converts monthly GB rate to hourly
3. **Public IP cost** -- Hourly rate for public IPv4
4. **OS license cost** -- Per-vCPU-hour surcharge (Windows, RHEL, etc.)
5. **Cross-AZ transfer** -- Intra-region transfer costs

The TCO result is a tuple: `(total, egress, storage, public_ip, os_license, cross_az)`.

---

## API Endpoints

All endpoints are mounted under the FastAPI application. API key authentication is enforced via `app/core/security.py`.

| Method | Path | Router File | Description |
|--------|------|-------------|-------------|
| POST | `/api/recommendations` | `app/api/recommendations.py` | Ranked instance recommendations by specs, region, and price constraints |
| GET | `/api/providers` | `app/api/providers.py` | List all ingested providers |
| GET | `/api/regions` | `app/api/regions.py` | List regions with optional provider filter, sustainability data |
| GET | `/api/instance-types` | `app/api/instance_types.py` | List instance types with pagination and filters |
| GET | `/api/prices` | `app/api/prices.py` | Query current prices with filters |
| GET | `/api/price-analytics/changes` | `app/api/price_analytics.py` | Detect recent price changes |
| GET | `/api/price-analytics/trends` | `app/api/price_analytics.py` | Analyze price trends over time |
| GET | `/health` | `app/api/health.py` | Health check (database connectivity) |
| GET | `/api/metrics` | `app/api/metrics.py` | Application metrics |

### Pagination

List endpoints use `app/api/_pagination.py` which provides a `PaginatedQuery` builder supporting `limit` and `offset` query parameters.

---

## Deployment

### Local Development

```bash
docker-compose up -d          # PostgreSQL
cp .env.example .env          # Configure credentials
python -m apps.ingestor ingest --provider aws   # Ingest data
uvicorn app.main:app --reload                   # Start API
```

### Kubernetes (Helm)

The Helm chart in `helm/cloudbroker/` deploys:
- FastAPI application (Deployment + Service + Ingress)
- Alembic migrations (Job)
- Ingestion CronJobs (one per provider)
- Kubernetes Secrets mounted at `/run/secrets/cloudbroker`

### Makefile

The `Makefile` provides shortcuts for all common operations:
- `make ingest-<provider>` -- Run ingestion for a specific provider
- `make ingest-all` -- Run all provider ingestions
- `make verify-<provider>` -- Verify ingested data against live API
- `make test` -- Run test suite
