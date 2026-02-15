"""GCP egress rate connector: fetch Network Egress pricing from Cloud Billing Catalog."""

from __future__ import annotations

import asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur, to_eur
from app.core.logging import get_logger
from app.services.ingestion import ensure_egress_rate

logger = get_logger(__name__)


def _get_billing_client():
    try:
        from google.cloud import billing_v1
    except ImportError:
        raise RuntimeError(
            "google-cloud-billing is required for GCP egress. Install with: pip install google-cloud-billing"
        )
    return billing_v1.CloudCatalogClient()


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
    client = _get_billing_client()
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
    egress_usd, free_gb, cross_usd = await asyncio.to_thread(_fetch_egress_rates_gcp)
    rate_usd_eur = await get_rate_to_eur(db, "USD")
    egress_eur = to_eur(egress_usd, rate_usd_eur)
    cross_eur = to_eur(cross_usd, rate_usd_eur) if cross_usd else None
    # Cross-AZ (same region): GCP typically ~0.01 USD/GB
    cross_az_usd = 0.01
    cross_az_eur = to_eur(cross_az_usd, rate_usd_eur)
    await ensure_egress_rate(
        db,
        provider_slug="gcp",
        egress_eur_per_gb=round(egress_eur, 6),
        free_tier_gb_per_month=free_gb,
        cross_region_eur_per_gb=round(cross_eur, 6) if cross_eur else None,
        cross_az_eur_per_gb=round(cross_az_eur, 6),
    )
    logger.info(
        "Ingested GCP egress rate",
        egress_eur_per_gb=round(egress_eur, 4),
        free_tier_gb_per_month=free_gb,
    )
