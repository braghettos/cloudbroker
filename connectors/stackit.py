"""STACKIT connector: config-based ingestion (no pricing API)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.price import PurchaseModel
from app.models.provider import ProviderType
from app.services.ingestion import (
    ensure_instance_type,
    ensure_provider,
    ensure_region,
    append_price,
)

logger = get_logger(__name__)

# STACKIT regions (DE, AT)
STACKIT_REGIONS = [
    {"slug": "eu01", "name": "EU01", "country": "DE", "is_eu": True},
    {"slug": "eu02", "name": "EU02", "country": "DE", "is_eu": True},
    {"slug": "at01", "name": "AT01", "country": "AT", "is_eu": True},
]

# Instance type: (vcpu, ram_gb, price_eur_per_hour) - from stackit.de/pricing
STACKIT_FLAVORS = {
    "c2a.1d": (1, 2, 0.05),
    "c2a.2d": (2, 4, 0.10),
    "c2a.4d": (4, 8, 0.20),
    "c2a.8d": (8, 16, 0.40),
    "c2a.16d": (16, 32, 0.80),
    "c3i.1": (1, 2, 0.05),
    "c3i.2": (2, 4, 0.10),
    "c3i.4": (4, 8, 0.20),
    "c3i.8": (8, 16, 0.40),
    "g2a.1d": (1, 4, 0.06),
    "g2a.2d": (2, 8, 0.12),
    "g2a.4d": (4, 16, 0.24),
    "g2a.8d": (8, 32, 0.48),
}


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest STACKIT instance types and pricing from config."""
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning(
            "STACKIT only supports on-demand pricing, ignoring purchase_model",
            purchase_model=purchase_model,
        )
        purchase = PurchaseModel.ON_DEMAND

    provider = await ensure_provider(
        db,
        slug="stackit",
        name="STACKIT",
        website="https://stackit.de",
        type=ProviderType.EU,
    )
    await db.flush()

    region_by_slug = {}
    for reg in STACKIT_REGIONS:
        if only_eu and not reg["is_eu"]:
            continue
        region = await ensure_region(
            db,
            provider_id=provider.id,
            slug=reg["slug"],
            name=reg["name"],
            country=reg["country"],
            is_eu=reg["is_eu"],
        )
        region_by_slug[reg["slug"]] = region
    await db.flush()

    inserted_prices = 0
    for flavor_name, (vcpu, ram_gb, price_eur) in STACKIT_FLAVORS.items():
        it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=flavor_name,
            vcpu=vcpu,
            ram_gb=float(ram_gb),
            family="compute",
            arch="x86_64",
        )
        await db.flush()

        for region in region_by_slug.values():
            p = await append_price(
                db,
                instance_type_id=it.id,
                region_id=region.id,
                purchase_model=purchase,
                currency="EUR",
                price_per_hour=price_eur,
                price_eur_per_hour=price_eur,
            )
            if p:
                inserted_prices += 1

    logger.info(
        "STACKIT ingestion completed",
        provider_slug="stackit",
        regions=len(region_by_slug),
        instance_types=len(STACKIT_FLAVORS),
        price_rows_inserted=inserted_prices,
    )
