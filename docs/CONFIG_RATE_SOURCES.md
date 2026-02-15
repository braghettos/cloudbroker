# Config-Based Rate Ingestion – Documentation Links

CloudBroker uses **config files** (YAML) for cost rates when providers do not expose public pricing APIs. This document lists the documentation sources used to populate and validate the config values.

## Overview

| Config File | Purpose | Providers Using Config |
|-------------|---------|-------------------------|
| [network_rates_config.yaml](../connectors/network_rates_config.yaml) | Egress, cross-region, cross-AZ rates | Hetzner, DigitalOcean, Scaleway, OVH |
| [storage_rates_config.yaml](../connectors/storage_rates_config.yaml) | Block storage (SSD/HDD/balanced) per GB-month | Hetzner, DigitalOcean, Scaleway, OVH |
| [public_ip_rates_config.yaml](../connectors/public_ip_rates_config.yaml) | Public IP cost per hour | All 7 (AWS, Azure, GCP, Hetzner, DigitalOcean, Scaleway, OVH) |
| [os_license_rates_config.yaml](../connectors/os_license_rates_config.yaml) | OS surcharges (Windows, RHEL) per vCPU-hour | All 7 providers |

## Provider Documentation Links

### Hetzner

| Cost Type | Documentation Link |
|-----------|--------------------|
| Egress / billing | https://docs.hetzner.com/general/others/billing/ |
| Block storage | https://www.hetzner.com/storage/storage-box |

### DigitalOcean

| Cost Type | Documentation Link |
|-----------|--------------------|
| Egress / bandwidth | https://docs.digitalocean.com/platform/billing/bandwidth/ |
| Block storage (Volumes) | https://docs.digitalocean.com/products/volumes/ |

### Scaleway

| Cost Type | Documentation Link |
|-----------|--------------------|
| Egress, storage, general pricing | https://www.scaleway.com/en/pricing/ |

### OVH

| Cost Type | Documentation Link |
|-----------|--------------------|
| Public cloud (egress, storage, etc.) | https://www.ovhcloud.com/en/public-cloud/prices/ |

### Hyperscalers (AWS, Azure, GCP)

For egress and storage, hyperscalers use **API-based** ingestion (AWS Pricing API, Azure Retail Prices, GCP Billing Catalog). The config files provide **fallback or supplemental** values for:

- **Public IP**: config used for all (e.g. [AWS Elastic IP](https://aws.amazon.com/ec2/pricing/on-demand/#Data_Transfer), [Azure Public IP](https://azure.microsoft.com/en-us/pricing/details/ip-addresses/), [GCP Static IP](https://cloud.google.com/vpc/network-pricing#ipaddress))
- **OS license**: config used for all (Windows/RHEL surcharges per provider pricing pages)

## Updating Config Values

When provider pricing changes:

1. Check the documentation links above for the relevant provider.
2. Update the corresponding YAML in `connectors/`.
3. Re-run the cost ingestion: `make ingest-all-costs` or `make ingest-storage`, etc.
