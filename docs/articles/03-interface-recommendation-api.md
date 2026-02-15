# How CloudBroker Picks the Cheapest VM: Constraints, Scoring, and the Recommendation API

**The interface: from "I need 2 vCPUs in Europe" to a ranked list.**

---

Send a JSON body with constraints; get a list of recommendations, best first. In between: **filter** (drop what doesn't qualify), then **score** (price + fit, or multi-criteria). Cheaper and better-fit rank higher. Every recommendation can include an **explain** block so you see why it ranked where it did.

---

## The request

Parameters you send to `POST /api/recommendations`:

- **min_vcpu**, **min_ram_gb** — minimum specs. Only instance types that meet or exceed these are considered.
- **arch** — `x86_64` or `arm64`.
- **region_constraint** — e.g. `"EU"` to restrict to EU regions (uses the `is_eu` flag on regions).
- **max_price_eur_per_hour** — ceiling in EUR. No candidate above this is returned.
- **allowed_providers** — list of provider slugs (e.g. `["gcp", "hetzner", "scaleway"]`). Only these providers are considered.

Optional: **preferred_providers** (score boost), **purchase_model** (on_demand, spot), **limit** (how many results).

**TCO (Total Cost of Ownership):** `data_source_provider`, `data_source_region`, `estimated_egress_gb_per_hour`, `max_tco_eur_per_hour`, `min_storage_gb`, `storage_type`, `include_public_ip`, `os_type`, `estimated_cross_az_gb_per_hour`.

**GPU:** `min_gpu_count`, `gpu_type` (e.g. nvidia-t4).

**Example:**

```json
{
  "min_vcpu": 2,
  "min_ram_gb": 4,
  "arch": "x86_64",
  "region_constraint": "EU",
  "max_price_eur_per_hour": 0.50,
  "allowed_providers": ["gcp", "hetzner", "scaleway"]
}
```

---

## The pipeline: filter, then score

**Filter:** Drop candidates that don't match specs (vcpu >= min, ram_gb >= min, arch match), price (<= max_price_eur_per_hour), region (e.g. is_eu when region_constraint is EU), and allowed providers. Only the latest price per (instance_type, region) is used.

**Score:** Two modes. **Legacy mode** (default): weighted blend of normalized price (cheaper = higher) and resource fit (closer to what you asked = higher). **Multi-criteria mode:** adds performance and reliability dimensions; hyperscalers get a higher reliability score than regional providers. After the base score, adjustments: preferred providers get a boost; USD-only prices can get a small penalty; spot gets bonus and interruption-risk penalty.

Results are sorted by score descending. Top of the list = recommendation.

---

## The response

Each item includes **provider_slug**, **region_slug**, **instance_type_name**, **price_eur_per_hour**, **score**, and **explain** (resource_fit, normalized_price, weights, region_is_eu, price_eur_per_hour, tco_eur_per_hour, egress_cost_eur_per_hour, storage_cost_eur_per_hour, etc. when TCO is used).

```json
{
  "instance_type_name": "cx23",
  "provider_slug": "hetzner",
  "region_slug": "fsn1",
  "vcpu": 2,
  "ram_gb": 4.0,
  "price_eur_per_hour": 0.0048,
  "score": 0.9898,
  "explain": {
    "resource_fit": 1.0,
    "normalized_price": 0.9797,
    "price_weight": 0.5,
    "fit_weight": 0.5,
    "region_is_eu": true,
    "price_eur_per_hour": 0.0048,
    "tco_eur_per_hour": null,
    "egress_cost_eur_per_hour": null,
    "storage_cost_eur_per_hour": null,
    "public_ip_cost_eur_per_hour": null,
    "os_license_cost_eur_per_hour": null,
    "cross_az_cost_eur_per_hour": null
  }
}
```

When TCO params are provided, `tco_eur_per_hour` and the cost components are populated.

---

## What CloudBroker doesn't do

No Kubernetes. No VM lifecycle. No notion of "who's calling" — it's a generic API. You (or another service) take the recommendation and provision elsewhere. CloudBroker is the price brain; something like Cloudburst Autoscaler is the provisioner.

---

So we have an API that, given constraints, returns the best option. The next piece is scope, limits, and how to run it.
