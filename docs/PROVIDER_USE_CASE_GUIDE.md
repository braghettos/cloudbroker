# Provider Use Case Guide: Private / Individual vs. Business

This document classifies CloudBroker-supported providers by their suitability for
**private/individual use** (hobbyist, developer, personal projects) versus **business/enterprise
use**, and explains the rationale behind each tier.

---

## Quick Reference

| Provider | Tier | HQ | Min price | Billing | Individual-friendly? |
|----------|------|----|-----------|---------|----------------------|
| **Hetzner** | 1 | DE | ~€3.49/mo | Hourly | ✅ Excellent |
| **Contabo** | 1 | DE | ~€3.60/mo | Monthly | ✅ Excellent |
| **Aruba Cloud** | 1 | IT | ~€2.79/mo | Hourly | ✅ Excellent |
| **Clouding.io** | 1 | ES | €0.004/hr | Hourly | ✅ Excellent |
| **Scaleway** | 1 | FR | ~€4.99/mo | Hourly | ✅ Excellent |
| **DigitalOcean** | 1 | US | ~€4/mo | Per-second | ✅ Excellent |
| **Cherry Servers** | 2 | LT | €0.015/hr | Hourly | ✅ Good |
| **UpCloud** | 2 | FI | ~€5/mo | Hourly | ✅ Good |
| **Exoscale** | 2 | CH | ~€7/mo | Hourly | ✅ Good |
| **Seeweb** | 2 | IT | ~€4/mo | Hourly | ✅ Good |
| **Gcore** | 2 | LU | ~€0.020/hr | Per-minute | ✅ Good |
| **OVHcloud** | 3 | FR | ~€3.82/mo | Hourly | ⚠️ Complex catalog |
| **AWS** | 4 | US | variable | Per-second | ❌ Enterprise |
| **Azure** | 4 | US | variable | Per-second | ❌ Enterprise |
| **GCP** | 4 | US | variable | Per-second | ❌ Enterprise |

---

## Tier 1 — Excellent for Individuals

Providers with the lowest entry prices, simple billing, no lock-in, and a clear
developer/individual focus.

### Hetzner Cloud
- **Entry:** CX22 — 2 vCPU, 2 GB RAM, 20 GB SSD — ~€3.49/mo (hourly billing)
- **Traffic:** 20 TB/mo included; additional at €0.001/GB
- **Why:** Cheapest EUR compute in Europe; German GDPR compliance; transparent pricing
- **Signup:** Self-service, no approval — https://console.hetzner.cloud/

### Contabo *(new)*
- **Entry:** Cloud VPS 10 — 4 vCPU, 8 GB RAM, 75 GB NVMe — €3.60/mo
- **Traffic:** Unlimited included in all plans
- **Why:** Exceptional spec-per-EUR; physics-based pricing; widely used by individual developers
- **Signup:** Self-service — https://contabo.com

### Aruba Cloud
- **Entry:** Smart XS — 1 vCPU, 1 GB RAM — ~€2.79/mo (hourly billing)
- **Traffic:** Unlimited included
- **Why:** Italy-focused; very simple flat plans; long track record
- **Signup:** Self-service — https://www.arubacloud.com

### Clouding.io *(new)*
- **Entry:** micro-1 — 1 vCPU, 1 GB RAM, 5 GB SSD — €0.004/hr (max €3/mo)
- **Traffic:** 4 TB/mo included in all plans
- **Why:** Ultra-affordable; Spain Tier IV DC; purely hourly with no minimum; ideal for experimentation
- **Signup:** Self-service, €5 free credit — https://clouding.io

### Scaleway
- **Entry:** DEV1-S — 2 vCPU, 2 GB RAM — ~€4.99/mo; PLAY2-NANO ARM — €1.99/mo
- **Traffic:** Free within regions; egress charged
- **Why:** French GDPR; ARM instances; developer-oriented with good API
- **Signup:** Self-service — https://www.scaleway.com

### DigitalOcean
- **Entry:** Basic Droplet — 1 vCPU, 512 MB RAM — ~€4/mo (per-second billing since 2026)
- **Traffic:** Fixed allocations per plan; predictable
- **Why:** Best developer UX; extensive docs; one-click apps; global
- **Signup:** Self-service — https://www.digitalocean.com

---

## Tier 2 — Good for Individuals

Slightly higher entry price or more complexity, but still accessible and practical for
individual use.

### Cherry Servers *(new)*
- **Entry:** virtual-ssd-1 — 1 vCPU, 1 GB RAM — €0.015/hr (~€11/mo)
- **Why:** Only provider with Baltic (Lithuania) coverage; hourly billing; crypto payment accepted
- **Regions:** LT, NL, DE, SE

### UpCloud
- **Entry:** 1xCPU-1GB — ~€5/mo; hourly billing
- **Why:** Finnish provider; clean API; carbon reporting; MaxIOPS storage

### Exoscale
- **Entry:** tiny — ~€7 CHF/mo; hourly billing
- **Why:** Swiss sovereignty and privacy; simple GDPR-compliant API; Swiss neutrality

### Seeweb
- **Entry:** Small plan — ~€4/mo
- **Why:** Italian sovereign cloud; simple plans; good for Southern EU use cases

### Gcore *(new)*
- **Entry:** g1-standard-1-2 — 1 vCPU, 2 GB RAM — ~€0.020/hr
- **Why:** Luxembourg HQ; 9 EU locations; competitive pricing; per-minute billing

---

## Tier 3 — Business/SMB Oriented

Accessible to individuals but primarily targeting SMBs. Onboarding or pricing may add friction.

### OVHcloud
- **Why:** France-based and sovereign, but the catalog is complex (flavors, project IDs, OVH API keys).
  VPS pricing from €3.82/mo is good, but standard Public Cloud requires project setup.



---

## Tier 4 — Enterprise Only

These providers are powerful and cost-effective at scale but not designed for individual use:
complex billing, USD pricing, IAM setup, and enterprise support models.

### AWS, Azure, GCP
- All three require credit card + account setup with AWS/Microsoft/Google accounts.
- Pricing is complex (hundreds of SKUs, spot/reserved models).
- Useful for individuals with existing accounts, but not the primary target audience.

---

## Removed Providers (Not Individual-Friendly)

The following providers were removed from CloudBroker because they are exclusively for
business customers:

| Provider | Reason for removal |
|----------|--------------------|
| **STACKIT** | Schwarz Group (Lidl/Kaufland) subsidiary; business account required; enterprise-only onboarding |
| **Open Telekom Cloud** | T-Systems/Deutsche Telekom offering; business customers only; no individual signup |
| **gridscale** | B2B focus; primarily targets MSPs and enterprise resellers; pricing requires partner agreement |
| **Elastx** | Swedish OpenStack provider targeting enterprise/SMB only; complex OpenStack onboarding; no individual signup path |
| **IONOS** | 🇩🇪 Germany | Company name mandatory on signup (enforced in Italy and several EU countries); business-oriented billing flow; no individual signup path in EU locales |
| **Cyso Cloud** | Dutch B2B/wholesale OpenStack provider; enterprise certifications (ISO 27001, NEN 7510 healthcare); partner/business onboarding only; no individual signup |

---

## Geographic Coverage After Update

```
EU members covered: DE, FR, NL, FI, SE, IT, CZ, AT, BG, PL, ES, LT, LU, IE (via hyperscalers)
Non-EU European covered: CH (Exoscale), GB (Hetzner, Cherry Servers)
New gaps addressed:
  - Baltics (LT) → Cherry Servers
  - Iberian Peninsula (ES) → Clouding.io
  - Luxembourg (LU) → Gcore
  - Switzerland (CH) → Exoscale
  - Germany budget (DE) → Contabo
Remaining gaps: NO, DK, PT, GR, RO, HU, HR
```
