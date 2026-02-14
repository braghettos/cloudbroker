# Where CloudBroker Gets Its Numbers: Ingestion Across Seven Clouds

**How it works: from seven APIs to one database.**

---

CloudBroker can't recommend what it doesn't know. Its database is filled by **ingestion**: connectors that call each provider's pricing or catalog API, pull instance types and hourly rates, and upsert into PostgreSQL. Each provider is different; the connectors do the dirty work. Result: one model — providers, regions, instance types, prices — comparable in EUR.

---

## The hierarchy

Four entities form the core:

- **Provider** — slug (e.g. `aws`, `hetzner`, `scaleway`) and type (hyperscaler, EU, regional).
- **Region** — data center location with country code and EU flag (used for region constraints like "EU only").
- **InstanceType** — name, vCPU, RAM, architecture, family. Unique per provider.
- **Price** — hourly price for an instance type in a region. Original currency and EUR-normalized. Append-only; the engine uses the latest price per (instance_type, region).

**FxRate** — lookup for USD→EUR (and others) so recommendations are apples-to-apples. A stub rate is seeded if missing.

```
Provider --> Region
    |
    +--> InstanceType --> Price <-- Region
                           |
                        FxRate (lookup)
```

---

## Ingestion in practice

You run ingestion via the CLI. Each provider has a dedicated connector; the Makefile wraps them:

```bash
make ingest-hetzner    # Hetzner Cloud API
make ingest-gcp        # GCP Billing Catalog (requires ADC)
make ingest-aws        # AWS EC2 Pricing API
make ingest-azure      # Azure Retail Prices (public API)
make ingest-scaleway   # Scaleway API
make ingest-digitalocean
make ingest-ovh
make ingest-all        # All of the above
```

Ingestion is **idempotent**. Providers, regions, and instance types are upserted by unique keys. Prices are **append-only** — duplicate (instance_type, region, same hour) are skipped. You can rerun at any time; the recommendation engine always uses the last ingested snapshot. You control how often it runs (e.g. daily cron).

---

## What's in scope for ingestion

Seven providers; on-demand (and optionally spot) pricing. What's *not* in scope: no real-time tickers, no reserved-instance marketplaces in this baseline. The project focuses on comparable, hourly, normalized data so the recommendation API can rank options.

---

With this data in place, the next step is the interface: how a request becomes a ranked list — the recommendation API.
