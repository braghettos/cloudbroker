"""Config-based egress rate ingestion for providers without pricing APIs."""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services.ingestion import ensure_egress_rate

logger = get_logger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / "network_rates_config.yaml"


def _load_config() -> dict:
    """Load network rates config. Returns dict provider_slug -> rate dict."""
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        data = yaml.safe_load(f)
    return data or {}


async def ingest_egress_from_config(
    db: AsyncSession,
    provider_slug: str,
) -> None:
    """Ingest egress rate for a provider from config file. Idempotent."""
    config = _load_config()
    entry = config.get(provider_slug)
    if not entry:
        logger.warning("No config entry for provider", provider_slug=provider_slug)
        return
    egress = float(entry.get("egress_eur_per_gb", 0))
    free = float(entry.get("free_tier_gb_per_month", 0))
    cross = entry.get("cross_region_eur_per_gb")
    cross_float = float(cross) if cross is not None else None
    cross_az = entry.get("cross_az_eur_per_gb")
    cross_az_float = float(cross_az) if cross_az is not None else None
    await ensure_egress_rate(
        db,
        provider_slug=provider_slug,
        egress_eur_per_gb=round(egress, 6),
        free_tier_gb_per_month=free,
        cross_region_eur_per_gb=round(cross_float, 6) if cross_float is not None else None,
        cross_az_eur_per_gb=round(cross_az_float, 6) if cross_az_float is not None else None,
    )
    logger.info(
        "Ingested egress rate from config",
        provider_slug=provider_slug,
        egress_eur_per_gb=egress,
    )


def get_config_providers() -> list[str]:
    """Return provider slugs that have config entries."""
    return list(_load_config().keys())
