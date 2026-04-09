"""Shared egress rate ingestion helper.

All three API-based egress connectors (AWS, GCP, Azure) follow the same tail pattern:
  1. Fetch raw rates as (egress_usd, free_gb, cross_region_usd)
  2. Convert USD -> EUR via FX
  3. Add cross-AZ at ~$0.01/GB
  4. Call ensure_egress_rate()

This module extracts that shared tail.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur, to_eur
from app.core.logging import get_logger
from app.services.ingestion import ensure_egress_rate

logger = get_logger(__name__)

# Cross-AZ rate is consistently ~$0.01/GB across all three hyperscalers
_CROSS_AZ_USD_PER_GB = 0.01


async def ingest_egress_from_usd(
    db: AsyncSession,
    provider_slug: str,
    egress_usd_per_gb: float,
    free_tier_gb: float,
    cross_region_usd_per_gb: float | None,
    cross_az_usd_per_gb: float = _CROSS_AZ_USD_PER_GB,
) -> None:
    """Convert USD egress rates to EUR and upsert into DB."""
    rate_usd_eur = await get_rate_to_eur(db, "USD")
    egress_eur = to_eur(egress_usd_per_gb, rate_usd_eur)
    cross_eur = to_eur(cross_region_usd_per_gb, rate_usd_eur) if cross_region_usd_per_gb is not None else None
    cross_az_eur = to_eur(cross_az_usd_per_gb, rate_usd_eur)

    await ensure_egress_rate(
        db,
        provider_slug=provider_slug,
        egress_eur_per_gb=round(egress_eur, 6),
        free_tier_gb_per_month=free_tier_gb,
        cross_region_eur_per_gb=round(cross_eur, 6) if cross_eur is not None else None,
        cross_az_eur_per_gb=round(cross_az_eur, 6),
    )
    logger.info(
        "Ingested egress rate",
        provider_slug=provider_slug,
        egress_eur_per_gb=round(egress_eur, 4),
        free_tier_gb_per_month=free_tier_gb,
    )
