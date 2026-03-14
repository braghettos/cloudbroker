# Pricing Data Sources

CloudBroker ingests instance pricing from the following APIs and data sources. Run `make verify-gcp` or `make verify-hetzner` after ingestion to double-check stored prices against the source API.

## Verification

| Provider | Verify command | Requirements |
|----------|----------------|--------------|
| GCP | `make verify-gcp` | Google Cloud ADC |
| Hetzner | `make verify-hetzner` | `HETZNER_API_TOKEN` |
| Azure | `make verify-azure` | None (public API) |
| OVH | `make verify-ovh` | None (public catalog) |
| DigitalOcean | `make verify-all` (runs DO) | `DIGITALOCEAN_API_TOKEN` |
| AWS | `make verify-all` (runs AWS) | AWS credentials |
| Scaleway | `make verify-all` (runs Scaleway) | `SCALEWAY_SECRET_KEY` |
| UpCloud | `make verify-all` (runs UpCloud) | `UPCLOUD_API_TOKEN` |
| Seeweb | `make verify-all` (runs Seeweb) | `SEEWEB_API_TOKEN` |
| Config (Aruba, Exoscale, Contabo, Gcore, Cherry, Clouding) | `make verify-config` | None |
| **All** | `make verify-all` | Runs all verifications (skips providers without credentials) |

## Provider APIs & Docs

| Provider | Data source | API / Docs | Auth |
|----------|-------------|------------|------|
| **GCP** | Cloud Billing Catalog | [Cloud Billing API](https://cloud.google.com/billing/docs/how-to/catalog-api) | ADC |
| **AWS** | Price List API, EC2 pricing | [AWS Price List API](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/price-changes.html), [EC2 API](https://docs.aws.amazon.com/ec2/) | AWS credentials |
| **Azure** | Retail Prices API | [Azure Retail Prices API](https://prices.azure.com/api/retail/prices) | None (public) |
| **Hetzner** | Cloud API | [Hetzner Cloud API](https://docs.hetzner.cloud/) — `GET /server_types`, `GET /locations` | [API token](https://docs.hetzner.cloud/#authentication) |
| **DigitalOcean** | REST API | [DigitalOcean API](https://docs.digitalocean.com/reference/api/) — sizes, regions | [API token](https://docs.digitalocean.com/reference/api/) |
| **Scaleway** | Instance API | [Scaleway Instance API](https://www.scaleway.com/en/developers/api/instance/) | [API keys](https://www.scaleway.com/en/developers/api/instance/) |
| **OVH** | OVH API / Public Catalog | [OVH API](https://api.ovh.com/) — `GET /cloud/project/{id}/flavor`, [Public Catalog](https://api.ovh.com/1.0/order/catalog/public/cloud) | OVH keys / none for catalog |
| **UpCloud** | REST API | [UpCloud API](https://developers.upcloud.com/1.2/) — `GET /plan`, `GET /zone`, `GET /price` | Username + password |
| **Seeweb** | ECS API | [Seeweb ECS API](https://api.seeweb.it/ecs/v2) | [API token](https://docs.seeweb.it/) |
| **Aruba** | Config | [Aruba Cloud pricing](https://www.arubacloud.com/pricing/) | None |
| **Exoscale** | Config | [Exoscale pricing](https://www.exoscale.com/pricing/) | None |

## Config-based providers

Config-based providers (Aruba, Exoscale, Contabo, Gcore, Cherry Servers, Clouding.io) use static pricing from the connector source code, derived from publicly published pricing pages. These are not verified against an API; update the config when provider pricing changes.
