"""Azure egress rate connector: fetch Data Transfer Out pricing from Retail Prices API."""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

AZURE_RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"
AZURE_API_VERSION = "2023-01-01-preview"

# Representative region for internet egress (Standard Data Transfer Out)
# We fetch westeurope as a baseline; Azure egress varies by region.
_REP_REGION = "westeurope"


def _is_egress_to_internet(item: dict) -> bool:
    """Filter for Standard Data Transfer Out (egress to internet), exclude inter-region/China etc."""
    name = (item.get("meterName") or "").lower()
    product = (item.get("productName") or "").lower()
    # Exclude: Inter-Region, Inter-AZ, China, Data Transfer In
    if "inter-region" in name or "inter-availability" in name or "inter-az" in name:
        return False
    if "china" in name or "china" in product:
        return False
    if "transfer in" in name or "data transfer in" in name:
        return False
    if "data transfer out" in name and "1 gb" in (item.get("unitOfMeasure") or "").lower():
        return True
    return False


def _extract_first_paid_tier_usd(items: list[dict]) -> tuple[float, float] | None:
    """
    From tiered egress items, extract first paid tier: (usd_per_gb, free_tier_gb).
    Azure: tierMinimumUnits 0 often = free tier; first tier with units > 0 is first paid.
    Free tier is typically 5 GB/month for first 5GB out (tier 0 = free).
    """
    gb_items = [i for i in items if "1 gb" in (i.get("unitOfMeasure") or "").lower()]
    paid = [i for i in gb_items if (i.get("tierMinimumUnits") or 0) > 0 and (i.get("retailPrice") or 0) > 0]
    if not paid:
        return None
    # Lowest tier (e.g. 100 GB) is typical first paid rate
    first = min(paid, key=lambda x: x.get("tierMinimumUnits") or 0)
    usd_per_gb = float(first.get("retailPrice") or 0)
    # Free tier: Azure often has 5 GB free/month
    free_items = [i for i in gb_items if (i.get("tierMinimumUnits") or 0) == 0 and (i.get("retailPrice") or 0) == 0]
    free_gb = 5.0 if free_items else 0.0  # Approximate
    return (usd_per_gb, free_gb)


async def fetch_egress_rates_azure() -> tuple[float, float, float | None]:
    """
    Fetch Azure internet egress rate from Retail Prices API.
    Returns (egress_usd_per_gb, free_tier_gb_per_month, cross_region_usd_per_gb).
    """
    params = {
        "api-version": AZURE_API_VERSION,
        "$filter": (f"serviceName eq 'Bandwidth' and armRegionName eq '{_REP_REGION}' and type eq 'Consumption'"),
        "$top": 100,
    }
    all_items: list[dict] = []
    next_link: str | None = None

    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            if next_link:
                url = next_link if next_link.startswith("http") else f"{AZURE_RETAIL_PRICES_URL.rstrip('/')}{next_link}"
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

    egress_items = [i for i in all_items if _is_egress_to_internet(i)]
    if not egress_items:
        logger.warning("Azure egress: no Standard Data Transfer Out items found")
        return (0.10, 0.0, 0.02)  # Fallback reference rate

    tier_data = _extract_first_paid_tier_usd(egress_items)
    if not tier_data:
        return (0.10, 0.0, 0.02)

    usd_per_gb, free_gb = tier_data
    # Cross-region: fetch Inter-Region Data Transfer for same region
    cross_items = [
        i
        for i in all_items
        if "inter-region data transfer" in (i.get("meterName") or "").lower()
        and "1 gb" in (i.get("unitOfMeasure") or "").lower()
        and (i.get("retailPrice") or 0) > 0
    ]
    cross_usd = float(cross_items[0]["retailPrice"]) if cross_items else usd_per_gb * 0.2

    return (usd_per_gb, free_gb, cross_usd)


async def ingest_egress_azure(db: AsyncSession) -> None:
    """Ingest Azure egress rates and upsert into egress_rates."""
    from connectors._egress_helper import ingest_egress_from_usd

    egress_usd, free_gb, cross_usd = await fetch_egress_rates_azure()
    await ingest_egress_from_usd(db, "azure", egress_usd, free_gb, cross_usd)
