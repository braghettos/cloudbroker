# Adding a New Cloud Provider to CloudBroker

This guide walks through every step required to add a new cloud provider connector, register it with the ingestion CLI, and verify the data lands correctly.

---

## 1. Choosing a Connector Type

### Option A: Config-Based (recommended for providers without public pricing APIs)

Use `BaseConfigConnector` from `connectors/_config_connector.py`. This base class handles all the boilerplate: provider creation, region iteration, instance type upsert, and price insertion. You only need to define metadata, regions, and plans.

Existing examples (and approximate line counts):

- `connectors/contabo.py` (~69 lines)
- `connectors/cherry_servers.py` (~58 lines)
- `connectors/gcore.py` (~63 lines)

**Steps:**

1. Create `connectors/{provider}.py`.
2. Subclass `BaseConfigConnector`. Define the required class attributes:
   - `PROVIDER_SLUG` -- lowercase slug used everywhere (DB, CLI, URLs).
   - `PROVIDER_NAME` -- human-readable name.
   - `PROVIDER_WEBSITE` -- homepage URL.
   - `PROVIDER_TYPE` -- typically `ProviderType.EU` or `ProviderType.HYPERSCALER`.
   - `REGIONS` -- list of `ConfigRegion(slug, name, country_code, is_eu)`.
3. Implement `get_plans()` returning `list[ConfigPlan]`. Each `ConfigPlan` accepts:
   - `name` (str) -- plan/instance name
   - `vcpu` (int) -- virtual CPU count
   - `ram_gb` (float) -- RAM in GiB
   - `eur_per_hour` (float) -- hourly price in EUR
   - `family` (str) -- see Normalization Checklist below
   - `cpu_type` (str | None) -- `"shared"`, `"dedicated"`, or `None`
   - `arch` (str) -- defaults to `"x86_64"`
   - `is_current_gen` (bool | None) -- defaults to `True`
   - `disk_gb` (float | None) -- local disk size, if included
   - `disk_type` (str | None) -- e.g. `"local_ssd"`, `"local_nvme"`
4. Add a module-level `async def ingest(db, only_eu=True, purchase_model="on_demand")` function that delegates to the connector class. This is required for CLI compatibility.

**Complete minimal example (~40 lines):**

```python
"""Example Cloud connector: config-based pricing.

Plans and pricing sourced from https://example-cloud.eu/pricing.
"""

from __future__ import annotations

from app.models.provider import ProviderType
from connectors._config_connector import BaseConfigConnector, ConfigPlan, ConfigRegion


class ExampleCloudConnector(BaseConfigConnector):
    PROVIDER_SLUG = "example_cloud"
    PROVIDER_NAME = "Example Cloud"
    PROVIDER_WEBSITE = "https://example-cloud.eu"
    PROVIDER_TYPE = ProviderType.EU

    REGIONS = [
        ConfigRegion("eu-de-1", "Frankfurt (Germany)", "DE", True),
        ConfigRegion("eu-fi-1", "Helsinki (Finland)", "FI", True),
    ]

    _PLANS = {
        # (vcpu, ram_gb, eur_per_hour)
        "small":  (2,  4.0, 0.025),
        "medium": (4,  8.0, 0.050),
        "large":  (8, 16.0, 0.100),
    }

    @classmethod
    def get_plans(cls) -> list[ConfigPlan]:
        return [
            ConfigPlan(
                name=name,
                vcpu=vcpu,
                ram_gb=ram_gb,
                eur_per_hour=eur_per_hour,
                family="general-purpose",
                cpu_type="dedicated",
            )
            for name, (vcpu, ram_gb, eur_per_hour) in cls._PLANS.items()
        ]


async def ingest(db, only_eu=True, purchase_model="on_demand"):
    await ExampleCloudConnector.ingest(db, only_eu, purchase_model)
```

### Option B: API-Based

For providers that expose a pricing API (e.g., Hetzner, DigitalOcean, AWS, GCP). These connectors fetch data at runtime and are more complex but stay up-to-date automatically.

Existing examples: `connectors/hetzner.py`, `connectors/digitalocean.py`.

**Steps:**

1. Create `connectors/{provider}.py` with an `async def ingest(db, only_eu, purchase_model)` entry point.
2. Pull credentials from the `Settings` class in `app/core/config.py` -- never read `os.environ` directly. Example:

   ```python
   from app.core.config import settings

   token = settings.example_cloud_api_token
   ```

3. Fetch data from the provider API using `httpx.AsyncClient`.
4. For each instance type, call the shared ingestion helpers:

   ```python
   from app.services.ingestion import append_price, ensure_instance_type, ensure_provider, ensure_region

   provider = await ensure_provider(db, slug=..., name=..., website=..., type=...)
   region = await ensure_region(db, provider_id=provider.id, slug=..., name=..., country=..., is_eu=...)
   it = await ensure_instance_type(db, provider_id=provider.id, name=..., vcpu=..., ram_gb=..., family=..., arch=..., cpu_type=..., is_current_gen=...)
   await append_price(db, instance_type_id=it.id, region_id=region.id, purchase_model=purchase, currency="EUR", price_per_hour=..., price_eur_per_hour=...)
   ```

5. If the API returns prices in a non-EUR currency, convert to EUR before calling `append_price`. Store the original currency/amount in `currency` and `price_per_hour`, and the converted amount in `price_eur_per_hour`.

---

## 2. Normalization Checklist

Every connector MUST set these fields consistently:

| Field            | Allowed values                                                                 |
|------------------|-------------------------------------------------------------------------------|
| `family`         | `general-purpose`, `compute-optimized`, `memory-optimized`, `storage-optimized`, `gpu` |
| `arch`           | `x86_64` or `arm64`                                                           |
| `cpu_type`       | `shared`, `dedicated`, or `None` (if unknown)                                 |
| `is_current_gen` | `True`, `False`, or `None`                                                    |

These values are used across the recommendation engine, filtering APIs, and analytics. Inconsistent values will cause the provider's instances to be excluded from comparisons.

---

## 3. Registering the Provider

Four files need changes. All references below use line numbers from the current codebase -- they may drift as the file evolves, but the surrounding context should make the location obvious.

### 3a. Add to `PROVIDERS_ALL` in `apps/ingestor/cli.py` (line 115)

Append the provider slug to the `PROVIDERS_ALL` list:

```python
PROVIDERS_ALL = [
    ...
    "ibm_cloud",
    "example_cloud",   # <-- add here
]
```

### 3b. Add provider choice to argparse in `_parse()` (line 34)

Add the slug to the `choices` list of the `--provider` argument:

```python
ing.add_argument(
    "--provider",
    choices=[..., "ibm_cloud", "example_cloud", "sustainability", "all"],
    ...
)
```

### 3c. Import and add to `conn_map` in `_ingest_provider_instance()` (line 167)

Add the import and mapping entry:

```python
from connectors import example_cloud as example_cloud_conn

conn_map = {
    ...
    "ibm_cloud": ibm_cloud_conn,
    "example_cloud": example_cloud_conn,
}
```

### 3d. Add a Makefile target

Add a convenience target to the Makefile:

```makefile
ingest-example_cloud:
	$(VENV)/python -m apps.ingestor ingest --provider example_cloud
```

---

## 4. Adding Cost Components (optional)

CloudBroker tracks costs beyond compute pricing. Each is optional but improves recommendation accuracy.

### Egress

- **API-based:** Create `connectors/egress_{provider}.py` with its own `async def ingest(...)`.
- **Config-based:** Add entries to `connectors/network_rates_config.yaml`.

### Storage

- **API-based:** Create `connectors/storage_{provider}.py`.
- **Config-based:** Add entries to `connectors/storage_rates_config.yaml`.

### Public IP

Add entries to `connectors/public_ip_rates_config.yaml`.

### OS License

Add entries to `connectors/os_license_rates_config.yaml`.

---

## 5. Adding Credentials (API-based connectors only)

Config-based connectors do not need credentials. For API-based connectors:

### 5a. Add a field to the Settings class

In `app/core/config.py`, add a new field to the `Settings` class:

```python
class Settings(BaseSettings):
    ...
    example_cloud_api_token: str | None = None
```

### 5b. Add to `.env.example`

```
EXAMPLE_CLOUD_API_TOKEN=
```

### 5c. Add to the Bitwarden External Secrets template

In `helm/cloudbroker/examples/bitwarden-external-secrets.yaml`, add the new secret key so Kubernetes deployments can source it from Bitwarden.

---

## 6. Testing

### Run the connector

```bash
.venv/bin/python -m apps.ingestor ingest --provider example_cloud
```

### Verify data landed in the database

```bash
psql -d cloudbroker -c "
  SELECT p.slug, count(it.id)
  FROM providers p
  JOIN instance_types it ON it.provider_id = p.id
  WHERE p.slug = 'example_cloud'
  GROUP BY 1;
"
```

### Run unit tests

```bash
.venv/bin/python -m pytest tests/test_connector_parsing.py -v
```

If the connector does non-trivial parsing or mapping, add test cases to `tests/test_connector_parsing.py` covering edge cases (missing fields, zero prices, currency conversion, etc.).

---

## Quick Reference: File Checklist

| What                        | File                                                        |
|-----------------------------|-------------------------------------------------------------|
| Connector implementation    | `connectors/{provider}.py`                                  |
| Config connector base class | `connectors/_config_connector.py`                           |
| CLI registration            | `apps/ingestor/cli.py`                                      |
| Makefile target             | `Makefile`                                                  |
| Credentials (if needed)     | `app/core/config.py`, `.env.example`                        |
| K8s secrets (if needed)     | `helm/cloudbroker/examples/bitwarden-external-secrets.yaml` |
| Egress rates                | `connectors/egress_{provider}.py` or `connectors/network_rates_config.yaml` |
| Storage rates               | `connectors/storage_{provider}.py` or `connectors/storage_rates_config.yaml` |
| Public IP rates             | `connectors/public_ip_rates_config.yaml`                    |
| OS license rates            | `connectors/os_license_rates_config.yaml`                   |
| Tests                       | `tests/test_connector_parsing.py`                           |
