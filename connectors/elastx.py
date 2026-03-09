"""Elastx connector: config-based ingestion (OpenStack flavors, pricing from elastx.se)."""

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

# Elastx regions (Sweden)
ELASTX_REGIONS = [
    {"slug": "se-sto", "name": "Stockholm", "country": "SE", "is_eu": True},
]

# Instance type: (vcpu, ram_gb, price_eur_per_hour) - SEK converted at ~0.085
# v1-c1-m0.5-d20: 0.104 SEK/hr -> ~0.009 EUR
# v1-c8-m64-d480: 6.34 SEK/hr -> ~0.54 EUR
ELASTX_FLAVORS = {
    "v1-c1-m0.5-d20": (1, 0.5, 0.009),
    "v1-c2-m1-d40": (2, 1, 0.018),
    "v1-c4-m2-d80": (4, 2, 0.036),
    "v1-c4-m4-d80": (4, 4, 0.072),
    "v1-c8-m8-d160": (8, 8, 0.144),
    "v1-c8-m16-d160": (8, 16, 0.288),
    "v1-c8-m32-d320": (8, 32, 0.432),
    "v1-c8-m64-d480": (8, 64, 0.54),
    "v2-c1-m0.5-d20": (1, 0.5, 0.025),
    "v2-c2-m1-d40": (2, 1, 0.05),
    "v2-c4-m2-d80": (4, 2, 0.10),
    "v2-c4-m4-d80": (4, 4, 0.20),
    "v2-c8-m8-d160": (8, 8, 0.40),
    "v2-c8-m16-d160": (8, 16, 0.80),
}


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest Elastx instance types and pricing from config."""
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning(
            "Elastx only supports on-demand pricing, ignoring purchase_model",
            purchase_model=purchase_model,
        )
        purchase = PurchaseModel.ON_DEMAND

    provider = await ensure_provider(
        db,
        slug="elastx",
        name="Elastx",
        website="https://elastx.se",
        type=ProviderType.EU,
    )
    await db.flush()

    region_by_slug = {}
    for reg in ELASTX_REGIONS:
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
    for flavor_name, (vcpu, ram_gb, price_eur) in ELASTX_FLAVORS.items():
        it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=flavor_name,
            vcpu=vcpu,
            ram_gb=float(ram_gb),
            family="openstack",
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
        "Elastx ingestion completed",
        provider_slug="elastx",
        regions=len(region_by_slug),
        instance_types=len(ELASTX_FLAVORS),
        price_rows_inserted=inserted_prices,
    )
