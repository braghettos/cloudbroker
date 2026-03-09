"""gridscale connector: config-based ingestion (pricing requires contract, use config)."""

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

# gridscale regions (DE, AT, NL, CH)
GRIDSCALE_REGIONS = [
    {"slug": "de-fra", "name": "Frankfurt", "country": "DE", "is_eu": True},
    {"slug": "de-dus", "name": "Düsseldorf", "country": "DE", "is_eu": True},
    {"slug": "at-vie", "name": "Vienna", "country": "AT", "is_eu": True},
    {"slug": "nl-ams", "name": "Amsterdam", "country": "NL", "is_eu": True},
    {"slug": "ch-zrh", "name": "Zurich", "country": "CH", "is_eu": False},
]

# Instance type: (vcpu, ram_gb, price_eur_per_hour) - config from gridscale.io/pricing
# gridscale uses custom cores/RAM; these are typical sizes
GRIDSCALE_FLAVORS = {
    "1c-2g": (1, 2, 0.02),
    "2c-4g": (2, 4, 0.04),
    "4c-8g": (4, 8, 0.08),
    "4c-16g": (4, 16, 0.12),
    "8c-16g": (8, 16, 0.16),
    "8c-32g": (8, 32, 0.24),
    "16c-32g": (16, 32, 0.32),
    "16c-64g": (16, 64, 0.48),
    "32c-64g": (32, 64, 0.64),
    "32c-128g": (32, 128, 0.96),
}


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest gridscale instance types and pricing from config."""
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning(
            "gridscale only supports on-demand pricing, ignoring purchase_model",
            purchase_model=purchase_model,
        )
        purchase = PurchaseModel.ON_DEMAND

    provider = await ensure_provider(
        db,
        slug="gridscale",
        name="gridscale",
        website="https://gridscale.io",
        type=ProviderType.EU,
    )
    await db.flush()

    region_by_slug = {}
    for reg in GRIDSCALE_REGIONS:
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
    for flavor_name, (vcpu, ram_gb, price_eur) in GRIDSCALE_FLAVORS.items():
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
        "gridscale ingestion completed",
        provider_slug="gridscale",
        regions=len(region_by_slug),
        instance_types=len(GRIDSCALE_FLAVORS),
        price_rows_inserted=inserted_prices,
    )
