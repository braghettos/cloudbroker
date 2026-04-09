"""Azure storage rate connector: fetch Managed Disk pricing from Retail Prices API."""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

AZURE_RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"
_REP_REGION = "westeurope"


def _map_meter_to_type(meter_name: str) -> str:
    name = (meter_name or "").lower()
    if "premium" in name or "ssd" in name or "p1" in name or "p2" in name or "p3" in name:
        return "ssd"
    if "standard" in name and ("hdd" in name or "s1" in name or "s2" in name):
        return "hdd"
    if "standard ssd" in name or "e1" in name or "e2" in name:
        return "balanced"
    return "balanced"


async def _fetch_azure_disk_items() -> list[dict]:
    """Fetch Azure disk pricing items."""
    params = {
        "api-version": "2023-01-01-preview",
        "$filter": f"serviceName eq 'Storage' and armRegionName eq '{_REP_REGION}' and contains(productName, 'Disk')",
        "$top": 100,
    }
    all_items: list[dict] = []
    next_link = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            if next_link:
                url = next_link if next_link.startswith("http") else f"https://prices.azure.com{next_link}"
                r = await client.get(url)
            else:
                r = await client.get(AZURE_RETAIL_PRICES_URL, params=params)
            r.raise_for_status()
            data = r.json()
            items = data.get("Items") or []
            all_items.extend(items)
            next_link = data.get("NextPageLink")
            if not next_link:
                break
    return all_items


def _extract_disk_rates(items: list[dict]) -> list[tuple[str, float]]:
    """Extract (storage_type, usd_per_gb_month) from Azure items."""
    gb_items = [
        i
        for i in items
        if "gb" in (i.get("unitOfMeasure") or "").lower() and "month" in (i.get("unitOfMeasure") or "").lower()
    ]
    paid = [i for i in gb_items if (i.get("retailPrice") or 0) > 0]
    seen: set[str] = set()
    result: list[tuple[str, float]] = []
    for i in paid:
        stype = _map_meter_to_type(i.get("meterName") or "")
        if stype in seen:
            continue
        seen.add(stype)
        result.append((stype, float(i.get("retailPrice", 0))))
    return result


async def ingest_storage_azure(db: AsyncSession) -> None:
    """Ingest Azure storage rates."""
    from connectors._storage_helper import ingest_storage_from_usd

    items = await _fetch_azure_disk_items()
    rates = _extract_disk_rates(items)
    if not rates:
        logger.warning("Azure storage: no disk items found, using fallback")
        rates = [("ssd", 0.105), ("hdd", 0.045), ("balanced", 0.068)]
    await ingest_storage_from_usd(db, "azure", rates, region_slug="westeurope")
