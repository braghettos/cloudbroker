# European Cloud Providers: CloudBroker & Cloudburst Compatibility Analysis

**Source:** [European Alternatives - Cloud Computing Platforms](https://european-alternatives.eu/category/cloud-computing-platforms)  
**Date:** February 2025

This document analyzes European cloud providers listed on European Alternatives and assesses their compatibility with CloudBroker (pricing ingestion + recommendation API) and Cloudburst (Kubernetes burst node provisioning).

---

## Summary Matrix

| Provider | Country | CloudBroker | Cloudburst | Effort | Notes |
|----------|---------|-------------|------------|--------|-------|
| **Scaleway** | France | ✅ Supported | ✅ Supported | — | Already integrated |
| **OVHcloud** | France | ✅ Supported | ✅ Supported | — | Already integrated |
| **UpCloud** | Finland | 🟢 High | 🟢 High | Low | REST API, plans + pricing endpoints |
| **Exoscale** | Switzerland | 🟢 High | 🟢 High | Low | APIv2, instance types documented |
| **Aruba Cloud** | Italy | 🟢 High | 🟢 High | Low | REST API, was previously in CloudBroker |

---

## Requirements Recap

### CloudBroker Requirements
- **Pricing/catalog API** (or scrapeable source) for instance types and hourly prices
- **Data model:** Provider, Region, InstanceType (name, vCPU, RAM, arch, family), Price (hourly, EUR-normalized)
- **Connector:** Python async `ingest(db)` using `ensure_provider`, `ensure_region`, `ensure_instance_type`, `append_price`

### Cloudburst Requirements
- **Provisioner interface:** `CreateInstance`, `DeleteInstance`, `IsInstanceRunning`, `Close`
- **Cloud-init** (or equivalent) for startup scripts (Tailscale, containerd, kubeadm)
- **NodeClass config** + factory registration
- **Provider slug** must match CloudBroker

---

## Detailed Provider Analysis

### 1. Scaleway (France) — ✅ Already Supported
- **CloudBroker:** Connector exists (`connectors/scaleway.py`)
- **Cloudburst:** Provisioner exists (`internal/scaleway/`)
- **Status:** Fully integrated in both projects.

---

### 2. OVHcloud (France) — ✅ Already Supported
- **CloudBroker:** Connector exists (`connectors/ovh.py`)
- **Cloudburst:** Provisioner exists (`internal/ovh/`)
- **Status:** Fully integrated in both projects.

---

### 3. UpCloud (Finland) — 🟢 High Compatibility

| Aspect | Details |
|-------|---------|
| **Pricing API** | `GET /1.2/price` returns zone-specific pricing; `GET /1.2/plan` returns preconfigured plans (CPU, RAM, storage, transfer) |
| **Instance types** | Plans: 1xCPU-2GB, 2xCPU-4GB, etc.; custom plans supported |
| **Provisioning** | REST API for server create/delete; cloud-init via `user_data` |
| **Regions** | Multiple zones (e.g. Helsinki, Amsterdam, Frankfurt) |
| **Auth** | API username + password |

**CloudBroker:** Plans endpoint + pricing endpoint provide all needed data. Connector effort: **Low**.  
**Cloudburst:** Standard REST create/delete; cloud-init supported. Provisioner effort: **Low**.

---

### 4. Exoscale (Switzerland) — 🟢 High Compatibility

| Aspect | Details |
|-------|---------|
| **Instance types** | Sizes: tiny, micro, small, medium, large, huge, jumbo, mega, extra-large, titan, colossus |
| **API** | APIv2 REST; `POST /instance` for create; `GET /instance` for list |
| **Regions** | Multiple European locations |
| **Auth** | API key (restrictable to Compute) |

**CloudBroker:** Instance types and pricing need to be mapped from API or docs. Effort: **Low–Medium**.  
**Cloudburst:** Standard REST provisioning; user-data for cloud-init. Effort: **Low**.

---

---

### 6. Aruba Cloud (Italy) — 🟢 High Compatibility

| Aspect | Details |
|-------|---------|
| **API** | REST at `api.arubacloud.com`; `POST /projects/:projectId/providers/Aruba.Compute/cloudServers` |
| **Instance types** | Flavors: CSO1A2, CSO4A8, CSO16A32, etc. |
| **Provisioning** | Boot volume + VPC/subnet/security groups required |
| **Regions** | ITBG-Bergamo; multiple EU datacenters |

**Note:** Aruba connector was previously in CloudBroker (`connectors/aruba.py` deleted). Re-adding is feasible.

**CloudBroker:** Previous connector; restore. Effort: **Low**.  
**Cloudburst:** More complex (boot volume, VPC); similar to OVH. Effort: **Medium**.

---

---

## Recommended Integration Order

### Tier 1 — Quick wins (already removed or straightforward)
1. **Aruba Cloud** — Restore CloudBroker connector; add Cloudburst provisioner (more setup for VPC/volumes)

### Tier 2 — Strong API support
2. **UpCloud** — Plans + pricing API; clear data model
3. **Exoscale** — APIv2; instance types documented

---

## References

- [European Alternatives - Cloud Computing Platforms](https://european-alternatives.eu/category/cloud-computing-platforms)
- [UpCloud API - Plans](https://developers.upcloud.com/1.2/7-plans/)
- [UpCloud API - Pricing](https://developers.upcloud.com/1.2/4-pricing/)
- [Exoscale APIv2](https://openapi-v2.exoscale.com/)
- [Aruba Cloud API](https://api.arubacloud.com/)
