# Azure Retail Prices API Research

**Research Date**: February 2026  
**Task**: 6.3 - Azure API research  
**Status**: ✅ Complete

---

## Overview

Azure exposes **retail list prices** via the **Azure Retail Prices API**. It is a public, **unauthenticated** REST API, so no Service Principal or Managed Identity is required for read-only price discovery. VM pricing can be filtered by service name, region, and SKU.

---

## Endpoint

- **Base URL**: `https://prices.azure.com/api/retail/prices`
- **API version**: `api-version=2023-01-01-preview` (or current; check [docs](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices))
- **Method**: GET
- **Authentication**: **None** for public retail prices

---

## Query parameters (OData)

| Parameter   | Example / notes |
|------------|------------------|
| `$filter`  | OData expression, e.g. `serviceName eq 'Virtual Machines'` |
| `armRegionName` | Region (e.g. `eastus`, `westeurope`) |
| `currencyCode`  | `USD` (default/base) |
| `$skip`    | Pagination offset |
| `api-version`   | `2023-01-01-preview` |

**Example – VMs in a region:**

```
https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview&$filter=serviceName eq 'Virtual Machines' and armRegionName eq 'westeurope'
```

Filter is **case-sensitive**; use `'Virtual Machines'` exactly.

---

## Response and pagination

- Returns JSON with an array of price items (e.g. `Items`) and a **`NextPageLink`** for the next page.
- Typical fields per item: `armRegionName`, `currencyCode`, `effectiveStartDate`, `meterId`, `meterName`, `productName`, `retailPrice`, `skuName`, `type`, `unitOfMeasure`, etc.
- **type**: e.g. `Consumption` (pay-as-you-go), `Reservation`, `DevTestConsumption` (for Dev/Test subscription pricing).
- **unitOfMeasure**: Often `1 Hour` for VM compute; use this to normalize to per-hour USD.

---

## Authentication (when required)

For **retail prices**, no auth is needed. For other Cost Management APIs (e.g. actual usage, cost by resource), Azure uses:

- **Service Principal**: App registration with client ID, client secret, tenant ID; grant the app “Cost Management Reader” or “Reader” on the scope.
- **Managed Identity**: When running in Azure (VM, App Service, etc.), use the resource’s managed identity; no secrets in code.

For CloudBroker ingestion of **list prices only**, HTTP GET to the retail prices endpoint is sufficient without credentials.

---

## Implementation notes

1. **Currency**: Use `currencyCode=USD` and convert to EUR with existing FX logic if needed.
2. **Regions**: Map `armRegionName` (e.g. `westeurope`, `northeurope`) to our region slug/ID; identify EU regions for `only_eu` filtering.
3. **VM SKUs**: Filter `serviceName eq 'Virtual Machines'`; use `productName` (e.g. `Standard_D4s_v3`) as instance type name; parse vCPU/RAM from product name or separate catalog if needed.
4. **Pagination**: Follow `NextPageLink` until absent; optional client-side rate limiting to avoid throttling.
5. **Spot**: Check if `type` or meter names distinguish spot; otherwise a separate Azure spot pricing source may be needed (to be confirmed in Task 7.2).

---

## References

- [Azure Retail Prices API (Microsoft Learn)](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices)
- [Cost Management automation overview](https://learn.microsoft.com/en-us/azure/cost-management-billing/automate/automation-overview)

---

**Status**: ✅ Task 6.3 complete. Ready for Task 6.4 (Azure connector skeleton) and 6.5 (Azure VM sizes mapping).
