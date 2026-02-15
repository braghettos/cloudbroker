"""Config-based public IP rate ingestion."""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services.ingestion import ensure_public_ip_rate

logger = get_logger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / "public_ip_rates_config.yaml"


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


async def ingest_public_ip_from_config(db: AsyncSession, provider_slug: str) -> None:
    config = _load_config()
    entry = config.get(provider_slug)
    if entry is None:
        logger.warning("No public IP config for provider", provider_slug=provider_slug)
        return
    eur = float(entry.get("eur_per_hour", 0))
    await ensure_public_ip_rate(db, provider_slug=provider_slug, eur_per_hour=eur)
    logger.info("Ingested public IP rate from config", provider_slug=provider_slug)


def get_public_ip_config_providers() -> list[str]:
    return list(_load_config().keys())


# Add hyperscaler defaults to config for API fallback
PUBLIC_IP_FALLBACK_EUR = {"aws": 0.0046, "azure": 0.003, "gcp": 0.004}
