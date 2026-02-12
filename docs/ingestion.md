# Ingestion

This document describes how to ingest **real** compute pricing data from supported providers (GCP, Hetzner, AWS, Azure) into the CloudBroker database.

## AWS Pricing API Research

For AWS integration, see [AWS Pricing API Research](./aws_pricing_api_research.md) for API overview, authentication, rate limits, and usage patterns.

## Overview

- **Idempotency**: Providers, regions, and instance types are upserted by unique keys. Prices are append-only; duplicate (instance_type, region, purchase_model, same hour) are skipped.
- **Currency**: Prices are stored with original currency; `price_eur_per_hour` is set when known (or converted via stub FX). Recommendations prefer EUR and apply a small score penalty when only USD is available.
- **CLI**: `python -m apps.ingestor ingest --provider gcp|hetzner|all` with options `--only-eu` (default true) and `--purchase-model on_demand`.

## Hetzner Cloud

### Getting an API token

1. Sign in at [Hetzner Cloud Console](https://console.hetzner.cloud/).
2. Select a project (or create one).
3. Go to **Security** → **API Tokens**.
4. Click **Generate API token**, name it (e.g. `cloudbroker-ingest`), copy the token (it is shown only once).

### Running ingestion

```bash
export HETZNER_API_TOKEN="your-token"
python -m apps.ingestor ingest --provider hetzner
# Optional: --only-eu false to include all regions
```

### What is ingested

- **Locations**: From `GET /locations`; each location becomes a **Region** (slug, name, country, `is_eu`).
- **Server types**: From `GET /server_types`; each server type becomes an **InstanceType** (name, vcpu, ram_gb, arch).
- **Prices**: For each server type × location, the hourly price (EUR) is stored as a **Price** (on_demand, currency=EUR, `price_eur_per_hour` set).

Hetzner prices are in EUR; no FX conversion is used.

---

## Google Cloud (GCP)

### Authenticating (ADC – Application Default Credentials)

GCP ingestion uses the **Cloud Billing Catalog API** with Application Default Credentials.

1. **Install the client library** (if not already):
   ```bash
   pip install google-cloud-billing
   ```

2. **Enable the API** (in a GCP project used for billing/catalog access):
   - [Cloud Billing API](https://console.cloud.google.com/apis/library/cloudbilling.googleapis.com) – enable it.

3. **Credentials** (choose one):
   - **gcloud CLI**: Run `gcloud auth application-default login` and sign in. ADC will use this.
   - **Service account**: Create a key and set `GOOGLE_APPLICATION_CREDENTIALS` to the JSON key path. The service account needs no special roles for the **public** catalog (list services/SKUs).

### Running ingestion

```bash
# After ADC is set up (e.g. gcloud auth application-default login)
python -m apps.ingestor ingest --provider gcp
```

### MVP scope and limitations

- **Service**: The connector finds the service whose `displayName` contains **Compute Engine** and lists its SKUs.
- **Filtering**: SKUs are filtered to Linux VM compute (resource family/group indicating CPU and RAM).
- **Pseudo instance types**: For MVP we do **not** map each GCP machine type (e.g. n1-standard-4) to a row. Instead we create two pseudo types:
  - **gcp-compute-cpu**: Represents CPU pricing (per vCPU-hour). `metadata_json` holds `description` and `pseudo: true`.
  - **gcp-compute-ram**: Represents RAM pricing (per GB-hour). Same idea.
- **Regions**: Taken from SKU `serviceRegions`; each becomes a Region (slug, name, `is_eu` for european prefixes).
- **Currency**: Catalog often returns USD. We store original currency and compute `price_eur_per_hour` using the stub FX rate (or `fx_rates` table if populated).
- **Limitations**: You cannot directly compare “one n1-standard-4 VM” to “one Hetzner CPX31” in recommendations; GCP is per-CPU and per-RAM SKU. Full machine-type mapping would require a separate mapping layer.

---

## Amazon Web Services (AWS)

### Credentials

AWS ingestion uses the public **Pricing API** plus EC2 metadata via `boto3`. Credentials are taken from the standard AWS chain; in practice the easiest is to use environment variables loaded from `.env`:

```bash
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=eu-west-1
```

The IAM principal must have at least:

- `pricing:GetProducts`
- `ec2:DescribeInstanceTypes`

### Running ingestion

```bash
# Full EU ingest (all configured EU regions)
make ingest-aws

# Quick smoke test: single region (eu-west-1)
make ingest-aws-quick
```

### What is ingested

- **Provider**: `aws` (type `HYPERSCALER`).
- **Regions**: Static mapping of AWS regions (e.g. `eu-west-1`, `eu-north-1`) to `Region` rows with `is_eu` flag.
- **Instance types**: EC2 instance types derived from Pricing API SKUs; each becomes an `InstanceType` with:
  - `name` (e.g. `m6i.large`)
  - `vcpu`, `ram_gb`
  - `arch` mapped from the physical processor (`x86_64` or `arm64`)
  - `family` inferred from the EC2 family (m, c, r, etc.)
- **Prices**:
  - On‑demand: `purchase_model=on_demand`, currency=USD, `price_eur_per_hour` filled via FX.
  - Spot: `purchase_model=spot`, taken from EC2 Spot Price history and normalized to an hourly EUR price.

Ingestion is idempotent; rerunning will update instance types and append new hourly prices.

---

## Microsoft Azure

### Authentication

Azure ingestion uses the public **Retail Prices API** and does **not** require authentication.

### Running ingestion

```bash
# Full EU ingest (all configured EU regions)
make ingest-azure

# Quick smoke test: single region (westeurope)
make ingest-azure-quick
```

### What is ingested

- **Provider**: `azure` (type `HYPERSCALER`).
- **Regions**: Static list of Azure regions (e.g. `westeurope`, `northeurope`, `uksouth`, `switzerlandnorth`) mapped to `Region` rows with `is_eu`.
- **Instance types**:
  - VM sizes are taken from `armSkuName` / `skuName` in the Retail Prices API (e.g. `Standard_D4ds_v5`).
  - Only `Standard_*` SKUs are ingested for the MVP.
  - `vcpu` and `ram_gb` are derived from the SKU name using a heuristic (B‑series ~2 GB/vCPU, others ~4 GB/vCPU).
  - `family` is inferred from the SKU (D/E/F/B/M, etc.), `arch` is currently treated as `x86_64`.
- **Prices**:
  - On‑demand VM prices from the Retail Prices API (`serviceName eq 'Virtual Machines'`, `type eq 'Consumption'`).
  - Spot VM prices are identified heuristically by the presence of `Spot` in `skuName` / `productName` / `meterName` and stored with `purchase_model=spot`.
  - Prices are in USD; `price_eur_per_hour` is filled via FX for both on‑demand and spot.

As with other providers, ingestion is idempotent and append‑only for prices.

---

## FX (currency) normalization

- **Table**: `fx_rates` stores (from_currency, to_currency, rate, effective_date). Optional; used when converting to EUR.
- **Stub**: If no row exists for USD→EUR, the code uses a default rate (e.g. 0.92). Run ingestion once to seed stub: `ensure_stub_fx_rates()` is called at the start of ingest.
- **Storing prices**: Connectors set `price_eur_per_hour` when currency is EUR or after converting with the stub/table. Recommendations use `COALESCE(price_eur_per_hour, price_per_hour)` and prefer EUR; USD-only results get a small score penalty.

---

## Verifying ingestion

After running ingest:

1. **Providers**  
   `GET /api/providers` – should include `gcp`, `hetzner`, `aws`, `azure` after running the relevant ingestors.

2. **Regions**  
   `GET /api/regions?provider=hetzner|gcp|aws|azure` – should list ingested regions.

3. **Instance types**  
   `GET /api/instance-types?provider=hetzner|gcp|aws|azure` – should list server types / VM sizes / pseudo types for each provider.

4. **Prices**  
   `GET /api/prices?provider=hetzner|gcp|aws|azure&limit=50` – should return price rows.

5. **Recommendations**  
   `POST /api/recommendations` with e.g. `min_vcpu=2`, `min_ram_gb=4`, `arch=x86_64`, `region_constraint=EU`, `max_price_eur_per_hour=0.5`, `limit=5` – should return ranked results.  
   - With only GCP/Hetzner ingested, you’ll see those providers.  
   - After running `make ingest-aws` and/or `make ingest-azure`, AWS and Azure should start appearing in suitable scenarios.

---

## Makefile targets

| Target                   | Description                                                   |
|--------------------------|---------------------------------------------------------------|
| `make ingest-gcp`        | Ingest GCP (requires ADC and `google-cloud-billing`)         |
| `make ingest-gcp-machines` | Compute GCP machine type prices from unit prices (offline calc) |
| `make ingest-hetzner`    | Ingest Hetzner (requires `HETZNER_API_TOKEN`)                |
| `make ingest-aws`        | Ingest AWS EC2 (requires AWS credentials with Pricing/EC2)   |
| `make ingest-aws-quick`  | Ingest AWS single region (`eu-west-1`) for fast validation   |
| `make ingest-azure`      | Ingest Azure VM prices (Retail Prices API; no auth)          |
| `make ingest-azure-quick` | Ingest Azure single region (`westeurope`) for fast validation |
| `make ingest-all`        | Ingest GCP + Hetzner + AWS + Azure                           |

Install optional deps for ingestion:

```bash
pip install -e ".[ingest]"
```
