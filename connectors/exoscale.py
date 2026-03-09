"""Exoscale connector: config-based ingestion (instance types + pricing from exoscale.com/pricing)."""

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

# Exoscale zones (EU/EFTA)
EXOSCALE_REGIONS = [
    {"slug": "ch-gva-2", "name": "Geneva 2", "country": "CH", "is_eu": False},
    {"slug": "ch-gva-3", "name": "Geneva 3", "country": "CH", "is_eu": False},
    {"slug": "at-vie-1", "name": "Vienna 1", "country": "AT", "is_eu": True},
    {"slug": "at-vie-2", "name": "Vienna 2", "country": "AT", "is_eu": True},
    {"slug": "de-muc-1", "name": "Munich 1", "country": "DE", "is_eu": True},
    {"slug": "bg-sof-1", "name": "Sofia 1", "country": "BG", "is_eu": True},
]

# Instance type: (vcpu, ram_gb, price_eur_per_hour) - from exoscale.com/pricing (approx)
# Format: family_size
# Format: family-size (matches Exoscale API)
EXOSCALE_FLAVORS = {
    "standard-micro": (1, 0.5, 0.003),
    "standard-tiny": (1, 1, 0.006),
    "standard-small": (2, 2, 0.012),
    "standard-medium": (2, 4, 0.024),
    "standard-large": (4, 8, 0.048),
    "standard-extra-large": (4, 16, 0.096),
    "standard-huge": (8, 32, 0.192),
    "standard-mega": (12, 64, 0.384),
    "standard-titan": (16, 128, 0.768),
    "standard-jumbo": (24, 225, 1.35),
    "cpu-extra-large": (8, 16, 0.096),
    "cpu-huge": (16, 32, 0.192),
    "cpu-mega": (32, 64, 0.384),
    "cpu-titan": (40, 128, 0.768),
    "memory-extra-large": (2, 16, 0.072),
    "memory-huge": (4, 32, 0.144),
    "memory-mega": (8, 64, 0.288),
    "memory-titan": (12, 128, 0.576),
    "storage-extra-large": (4, 16, 0.096),
    "storage-huge": (8, 32, 0.192),
    "storage-mega": (12, 64, 0.384),
    "storage-titan": (16, 128, 0.768),
    "storage-jumbo": (24, 225, 1.35),
}


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest Exoscale instance types and pricing from config."""
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning(
            "Exoscale only supports on-demand pricing, ignoring purchase_model",
            purchase_model=purchase_model,
        )
        purchase = PurchaseModel.ON_DEMAND

    provider = await ensure_provider(
        db,
        slug="exoscale",
        name="Exoscale",
        website="https://www.exoscale.com",
        type=ProviderType.EU,
    )
    await db.flush()

    region_by_slug = {}
    for reg in EXOSCALE_REGIONS:
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
    for flavor_name, (vcpu, ram_gb, price_eur) in EXOSCALE_FLAVORS.items():
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
        "Exoscale ingestion completed",
        provider_slug="exoscale",
        regions=len(region_by_slug),
        instance_types=len(EXOSCALE_FLAVORS),
        price_rows_inserted=inserted_prices,
    )
