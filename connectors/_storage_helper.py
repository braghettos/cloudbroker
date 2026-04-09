"""Shared storage rate ingestion helper.

All three API-based storage connectors (AWS, GCP, Azure) follow the same tail pattern:
  1. Fetch raw rates as list of (storage_type, usd_per_gb_month)
  2. Convert USD -> EUR via FX
  3. Call ensure_storage_rate() for each type

This module extracts that shared tail.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur, to_eur
from app.core.logging import get_logger
from app.services.ingestion import ensure_storage_rate

logger = get_logger(__name__)


async def ingest_storage_from_usd(
    db: AsyncSession,
    provider_slug: str,
    rates: list[tuple[str, float]],
    region_slug: str | None = None,
) -> None:
    """Convert USD storage rates to EUR and upsert into DB.

    Args:
        rates: list of (storage_type, usd_per_gb_month)
        region_slug: optional representative region slug
    """
    rate_usd_eur = await get_rate_to_eur(db, "USD")
    for storage_type, usd_per_gb_month in rates:
        eur = to_eur(usd_per_gb_month, rate_usd_eur)
        await ensure_storage_rate(
            db,
            provider_slug=provider_slug,
            storage_type=storage_type,
            eur_per_gb_month=round(eur, 6),
            region_slug=region_slug,
        )
    logger.info("Ingested storage rates", provider_slug=provider_slug, count=len(rates))
