# European Cloud Providers: CloudBroker & Cloudburst Compatibility Analysis

**Source:** [European Alternatives - Cloud Computing Platforms](https://european-alternatives.eu/category/cloud-computing-platforms)  
**Date:** February 2025

This document analyzes 12 European cloud providers listed on European Alternatives and assesses their compatibility with CloudBroker (pricing ingestion + recommendation API) and Cloudburst (Kubernetes burst node provisioning).

---

## Summary Matrix

| Provider | Country | CloudBroker | Cloudburst | Effort | Notes |
|----------|---------|-------------|------------|--------|-------|
| **Scaleway** | France | ✅ Supported | ✅ Supported | — | Already integrated |
| **OVHcloud** | France | ✅ Supported | ✅ Supported | — | Already integrated |
| **UpCloud** | Finland | 🟢 High | 🟢 High | Low | REST API, plans + pricing endpoints |
| **Exoscale** | Switzerland | 🟢 High | 🟢 High | Low | APIv2, instance types documented |
| **gridscale** | Germany | 🟢 High | 🟢 High | Low | REST API, cloud-init, Python client |
| **IONOS** | Germany | 🟢 High | 🟢 High | Medium | Cloud API v6, pricing page |
| **Aruba Cloud** | Italy | 🟢 High | 🟢 High | Low | REST API, was previously in CloudBroker |
| **Elastx** | Sweden | 🟡 Medium | 🟡 Medium | Medium | OpenStack Nova; pricing may need scraping |
| **Cyso Cloud** | Netherlands | 🟡 Medium | 🟡 Medium | Medium | OpenStack-based; similar to OVH/Scaleway |
| **Open Telekom Cloud** | Germany | 🟡 Medium | 🟡 Medium | Medium | OpenStack; pricing API exists |
| **STACKIT** | Germany | 🟡 Medium | 🟡 Medium | Medium | IaaS API; pricing structure differs |

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

### 5. gridscale (Germany) — 🟢 High Compatibility

| Aspect | Details |
|-------|---------|
| **API** | REST API; Python client `gridscale_api_client_python` on GitHub |
| **Pricing** | Transparent, per-minute; €10.53/month base; consumption charts |
| **Provisioning** | Real-time; user-data/cloud-init supported |
| **Regions** | Germany, Austria, Switzerland, Netherlands |
| **Auth** | User UUID + API Token |

**CloudBroker:** Pricing may require API or config-based ingestion. Effort: **Low–Medium**.  
**Cloudburst:** Libcloud driver exists; cloud-init supported. Effort: **Low**.

---

### 6. IONOS (Germany) — 🟢 High Compatibility

| Aspect | Details |
|-------|---------|
| **API** | Cloud API v6 (`api.ionos.com/docs/cloud/v6/`); Server API for create/delete |
| **Instance types** | Basic Cube XS/S/M/L (1–8 vCPU, 2–16 GB RAM) |
| **Pricing** | Per-minute; Cloud Price Calculator; pricing page available |
| **Auth** | API credentials |

**CloudBroker:** Pricing page + calculator; may need config or scrape. Effort: **Medium**.  
**Cloudburst:** Full CRUD API for servers. Effort: **Low**.

---

### 7. Aruba Cloud (Italy) — 🟢 High Compatibility

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

### 8. Elastx (Sweden) — 🟡 Medium Compatibility

| Aspect | Details |
|-------|---------|
| **Platform** | OpenStack (Nova compute) |
| **API** | Standard OpenStack REST; application credentials |
| **Regions** | 3 AZs in Sweden (20 km apart) |
| **Pricing** | Not clearly exposed via API; may need manual config or scraping |

**CloudBroker:** OpenStack doesn’t typically expose pricing via API. Config-based or scrape. Effort: **Medium**.  
**Cloudburst:** OpenStack Nova is well-understood; similar to OVH/Cyso. Effort: **Medium**.

---

### 9. Cyso Cloud (Netherlands) — 🟡 Medium Compatibility

| Aspect | Details |
|-------|---------|
| **Platform** | OpenStack (Nova, Keystone) |
| **API** | Keystone + Nova; X-Auth-Token |
| **Regions** | Amsterdam (ams2), Frankfurt (fra) |
| **Pricing** | OpenStack; likely config or scrape |

**CloudBroker:** Same OpenStack pricing constraints as Elastx. Effort: **Medium**.  
**Cloudburst:** Standard OpenStack Nova. Effort: **Medium**.

---

### 10. Open Telekom Cloud (Germany) — 🟡 Medium Compatibility

| Aspect | Details |
|-------|---------|
| **Platform** | OpenStack (Deutsche Telekom) |
| **API** | OpenStack + Pricing API (`docs.otc.t-systems.com/price-calculator/api-ref/`) |
| **Pricing** | Pay-as-you-go hourly; Price Calculator API |
| **Regions** | German data centers |

**CloudBroker:** Pricing API exists; more structured than typical OpenStack. Effort: **Medium**.  
**Cloudburst:** OpenStack Nova. Effort: **Medium**.

---

### 11. STACKIT (Germany) — 🟡 Medium Compatibility

| Aspect | Details |
|-------|---------|
| **API** | STACKIT IaaS API; Terraform provider |
| **Instance types** | XS–XL; General Use, Processor Optimized, RAM Optimized |
| **Regions** | Germany, Austria |
| **Pricing** | Sovereign EU cloud; structure may differ from standard hourly model |

**CloudBroker:** Need to verify pricing API or docs. Effort: **Medium**.  
**Cloudburst:** IaaS API for VM create/delete. Effort: **Medium**.

---

## Recommended Integration Order

### Tier 1 — Quick wins (already removed or straightforward)
1. **Aruba Cloud** — Restore CloudBroker connector; add Cloudburst provisioner (more setup for VPC/volumes)

### Tier 2 — Strong API support
2. **UpCloud** — Plans + pricing API; clear data model
3. **Exoscale** — APIv2; instance types documented
4. **gridscale** — REST API, cloud-init, Python client

### Tier 3 — More integration work
5. **IONOS** — Mature API; pricing may need config
6. **Open Telekom Cloud** — Pricing API + OpenStack
7. **Elastx** — OpenStack; pricing config-based
9. **Cyso Cloud** — OpenStack
10. **STACKIT** — IaaS API; verify pricing model

---

## References

- [European Alternatives - Cloud Computing Platforms](https://european-alternatives.eu/category/cloud-computing-platforms)
- [UpCloud API - Plans](https://developers.upcloud.com/1.2/7-plans/)
- [UpCloud API - Pricing](https://developers.upcloud.com/1.2/4-pricing/)
- [Exoscale APIv2](https://openapi-v2.exoscale.com/)
- [gridscale API](https://github.com/gridscale/gridscale_api_client_python)
- [IONOS Cloud API v6](https://api.ionos.com/docs/cloud/v6/)
- [Aruba Cloud API](https://api.arubacloud.com/)
- [Elastx OpenStack](https://docs.elastx.cloud/docs/openstack-iaas/guides/api_access/)
- [Cyso Cloud API](https://cyso.cloud/docs/cloud/how-to-get-started-with-the-cyso-cloud-api/)
- [Open Telekom Cloud Pricing API](https://docs.otc.t-systems.com/price-calculator/api-ref/)
- [STACKIT Docs](https://docs.stackit.cloud/)
