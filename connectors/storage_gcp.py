"""GCP storage rate connector: fetch Persistent Disk pricing from Billing Catalog."""

from __future__ import annotations

import asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)


from connectors._cloud_clients import get_gcp_billing_client


def _is_persistent_disk_sku(sku) -> bool:
    try:
        cat = sku.category
        if not cat:
            return False
        rf = (cat.resource_family or "").upper()
        rg = (cat.resource_group or "").upper()
        return "STORAGE" in rf or "DISK" in rf or "PD" in rg or "DISK" in rg
    except Exception:
        return False


def _map_sku_to_type(sku) -> str:
    try:
        desc = (sku.description or "").lower()
        if "ssd" in desc or "pd-ssd" in desc:
            return "ssd"
        if "standard" in desc or "pd-standard" in desc or "hdd" in desc:
            return "hdd"
        if "balanced" in desc or "pd-balanced" in desc:
            return "balanced"
        return "balanced"
    except Exception:
        return "balanced"


def _extract_price_usd_per_gb_month(sku) -> float | None:
    try:
        pi = sku.pricing_info
        if not pi or len(pi) == 0:
            return None
        expr = pi[0].pricing_expression
        tiered = expr.tiered_rates
        if not tiered or len(tiered) == 0:
            return None
        unit = tiered[0].unit_price
        if not unit:
            return None
        nanos = unit.nanos or 0
        units = unit.units or 0
        price = int(units) + (int(nanos) / 1e9)
        # GCP often prices in GiB-month
        return price if price > 0 else None
    except Exception:
        return None


def _has_eu_region(sku) -> bool:
    try:
        regions = list(sku.service_regions) if sku.service_regions else []
        return any("europe" in r.lower() or r.lower().startswith("eu") for r in regions)
    except Exception:
        return False


def _fetch_storage_rates_gcp() -> list[tuple[str, float]]:
    client = get_gcp_billing_client()
    services = list(client.list_services())
    compute_service = None
    for s in services:
        if s.display_name and "Compute Engine" in s.display_name:
            compute_service = s
            break
    if not compute_service:
        return [("ssd", 0.085), ("hdd", 0.04), ("balanced", 0.06)]
    skus = list(client.list_skus(parent=compute_service.name))
    seen: set[str] = set()
    result: list[tuple[str, float]] = []
    for sku in skus:
        if not _is_persistent_disk_sku(sku) or not _has_eu_region(sku):
            continue
        stype = _map_sku_to_type(sku)
        if stype in seen:
            continue
        price = _extract_price_usd_per_gb_month(sku)
        if price and price > 0:
            seen.add(stype)
            result.append((stype, price))
    if not result:
        return [("ssd", 0.085), ("hdd", 0.04), ("balanced", 0.06)]
    return result


async def ingest_storage_gcp(db: AsyncSession) -> None:
    """Ingest GCP storage rates."""
    from connectors._storage_helper import ingest_storage_from_usd

    rates = await asyncio.to_thread(_fetch_storage_rates_gcp)
    await ingest_storage_from_usd(db, "gcp", rates, region_slug="europe-west1")
