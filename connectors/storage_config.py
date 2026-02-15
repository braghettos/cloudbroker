"""Config-based storage rate ingestion for providers without pricing APIs."""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services.ingestion import ensure_storage_rate

logger = get_logger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / "storage_rates_config.yaml"


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


async def ingest_storage_from_config(db: AsyncSession, provider_slug: str) -> None:
    """Ingest storage rates for a provider from config file."""
    config = _load_config()
    provider_data = config.get(provider_slug)
    if not provider_data:
        logger.warning("No storage config for provider", provider_slug=provider_slug)
        return
    for storage_type, entry in provider_data.items():
        if not isinstance(entry, dict):
            continue
        region = entry.get("region_slug")
        eur = float(entry.get("eur_per_gb_month", 0))
        await ensure_storage_rate(
            db,
            provider_slug=provider_slug,
            storage_type=storage_type,
            eur_per_gb_month=round(eur, 6),
            region_slug=region if region else None,
        )
    logger.info("Ingested storage rates from config", provider_slug=provider_slug)


def get_storage_config_providers() -> list[str]:
    return list(_load_config().keys())
