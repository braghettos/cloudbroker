"""IONOS Cloud connector: config-based ingestion (no pricing API)."""

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

# IONOS Cloud regions (EU)
IONOS_REGIONS = [
    {"slug": "de-fra", "name": "Frankfurt", "country": "DE", "is_eu": True},
    {"slug": "eu-central", "name": "EU Central", "country": "DE", "is_eu": True},
]

# Instance type: (vcpu, ram_gb, price_eur_per_hour)
# IONOS bills vCPU + RAM separately: ~0.012 EUR/vCPU/hr, ~0.002 EUR/GB/hr (approx)
IONOS_FLAVORS = {
    "c2-r4": (2, 4, 0.032),
    "c2-r8": (2, 8, 0.048),
    "c4-r8": (4, 8, 0.064),
    "c4-r16": (4, 16, 0.096),
    "c8-r16": (8, 16, 0.128),
    "c8-r32": (8, 32, 0.192),
    "c16-r32": (16, 32, 0.256),
    "c16-r64": (16, 64, 0.384),
    "c32-r64": (32, 64, 0.512),
    "c32-r128": (32, 128, 0.768),
}


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest IONOS Cloud instance types and pricing from config."""
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning(
            "IONOS only supports on-demand pricing, ignoring purchase_model",
            purchase_model=purchase_model,
        )
        purchase = PurchaseModel.ON_DEMAND

    provider = await ensure_provider(
        db,
        slug="ionos",
        name="IONOS Cloud",
        website="https://cloud.ionos.com",
        type=ProviderType.EU,
    )
    await db.flush()

    region_by_slug = {}
    for reg in IONOS_REGIONS:
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
    for flavor_name, (vcpu, ram_gb, price_eur) in IONOS_FLAVORS.items():
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
        "IONOS ingestion completed",
        provider_slug="ionos",
        regions=len(region_by_slug),
        instance_types=len(IONOS_FLAVORS),
        price_rows_inserted=inserted_prices,
    )
