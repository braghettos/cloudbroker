"""GCP egress rate connector: fetch Network Egress pricing from Cloud Billing Catalog."""

from __future__ import annotations

import asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)


from connectors._cloud_clients import get_gcp_billing_client


def _is_network_egress_sku(sku) -> bool:
    """Filter for Network Egress to Internet SKUs (exclude inter-region, in)."""
    try:
        cat = sku.category
        if not cat:
            return False
        rf = (cat.resource_family or "").upper()
        rg = (cat.resource_group or "").upper()
        ut = (cat.usage_type or "").upper()
        desc = (sku.description or "").upper()
        if "NETWORK" not in rf and "NETWORKING" not in rf:
            return False
        if "EGRESS" not in rg and "EGRESS" not in ut and "EGRESS" not in desc:
            return False
        if "INTERNAL" in ut or "INTER-REGION" in ut or "INGRESS" in ut:
            return False
        return True
    except Exception:
        return False


def _extract_egress_price_usd(sku) -> tuple[float, float] | None:
    """Extract (usd_per_gb, free_tier_gb) from Network Egress SKU. Price in nanos."""
    try:
        pi = sku.pricing_info
        if not pi or len(pi) == 0:
            return None
        expr = pi[0].pricing_expression
        tiered = expr.tiered_rates
        if not tiered or len(tiered) == 0:
            return None
        # First tier is often free; first paid tier has unit_price
        free_gb = 0.0
        usd_per_gb = 0.0
        for t in tiered:
            start = float(t.start_usage_amount or 0)
            unit = t.unit_price
            if not unit:
                continue
            nanos = unit.nanos or 0
            units = unit.units or 0
            price = int(units) + (int(nanos) / 1e9)
            if price <= 0:
                free_gb = max(free_gb, start)
            else:
                usd_per_gb = price
                break
        if usd_per_gb <= 0:
            return None
        return (usd_per_gb, free_gb)
    except Exception:
        return None


def _has_eu_region(sku) -> bool:
    """Check if SKU applies to an EU region (europe-west1 etc)."""
    try:
        regions = list(sku.service_regions) if sku.service_regions else []
        return any("europe" in r.lower() or r.lower().startswith("eu") for r in regions)
    except Exception:
        return False


def _fetch_egress_rates_gcp() -> tuple[float, float, float | None]:
    """
    Fetch GCP internet egress rate from Billing Catalog. Returns (usd_per_gb, free_gb, cross_usd).
    Uses europe-west1 as representative.
    """
    client = get_gcp_billing_client()
    services = list(client.list_services())
    compute_service = None
    for s in services:
        if s.display_name and "Compute Engine" in s.display_name:
            compute_service = s
            break
    if not compute_service:
        logger.warning("GCP egress: Compute Engine service not found")
        return (0.10, 0.0, 0.02)

    skus = list(client.list_skus(parent=compute_service.name))
    for sku in skus:
        if not _is_network_egress_sku(sku):
            continue
        if not _has_eu_region(sku):
            continue
        extracted = _extract_egress_price_usd(sku)
        if extracted:
            usd_per_gb, free_gb = extracted
            cross_usd = usd_per_gb * 0.2  # Inter-region typically ~20% of internet egress
            return (usd_per_gb, free_gb, cross_usd)

    logger.warning("GCP egress: no Network Egress SKU found")
    return (0.10, 0.0, 0.02)


async def ingest_egress_gcp(db: AsyncSession) -> None:
    """Ingest GCP egress rates and upsert into egress_rates."""
    from connectors._egress_helper import ingest_egress_from_usd

    egress_usd, free_gb, cross_usd = await asyncio.to_thread(_fetch_egress_rates_gcp)
    await ingest_egress_from_usd(db, "gcp", egress_usd, free_gb, cross_usd)
