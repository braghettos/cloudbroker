"""Config-based OS license rate ingestion."""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services.ingestion import ensure_os_license_rate

logger = get_logger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / "os_license_rates_config.yaml"


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


async def ingest_os_license_from_config(db: AsyncSession, provider_slug: str) -> None:
    config = _load_config()
    provider_data = config.get(provider_slug)
    if not provider_data:
        logger.warning("No OS license config for provider", provider_slug=provider_slug)
        return
    for os_type, eur_per_vcpu in provider_data.items():
        if isinstance(eur_per_vcpu, (int, float)):
            await ensure_os_license_rate(
                db,
                provider_slug=provider_slug,
                os_type=os_type,
                eur_per_vcpu_hour=float(eur_per_vcpu),
            )
    logger.info("Ingested OS license rates from config", provider_slug=provider_slug)


def get_os_license_config_providers() -> list[str]:
    return list(_load_config().keys())
