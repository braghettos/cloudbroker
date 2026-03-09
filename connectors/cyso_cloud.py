"""Cyso Cloud connector: config-based ingestion (OpenStack flavors, pricing from cyso.cloud)."""

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

# Cyso Cloud regions (Netherlands)
CYSOCLOUD_REGIONS = [
    {"slug": "ams", "name": "Amsterdam", "country": "NL", "is_eu": True},
]

# Instance type: (vcpu, ram_gb, price_eur_per_hour) - from cyso.cloud/pricing
CYSOCLOUD_FLAVORS = {
    "s5.small": (2, 8, 0.026),
    "s5.medium": (4, 16, 0.052),
    "s5.large": (8, 32, 0.104),
    "s5.xlarge": (16, 64, 0.208),
    "s5.2xlarge": (32, 128, 0.416),
    "s5.4xlarge": (64, 256, 0.893),
    "c5.small": (2, 4, 0.033),
    "c5.medium": (4, 8, 0.065),
    "c5.large": (8, 16, 0.130),
    "c5.xlarge": (16, 32, 0.260),
    "c5.4xlarge": (64, 128, 1.042),
    "m5.small": (2, 16, 0.040),
    "m5.medium": (4, 32, 0.080),
    "m5.large": (8, 64, 0.160),
    "m5.xlarge": (16, 128, 0.320),
    "m5.4xlarge": (64, 512, 1.265),
}


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest Cyso Cloud instance types and pricing from config."""
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning(
            "Cyso Cloud only supports on-demand pricing, ignoring purchase_model",
            purchase_model=purchase_model,
        )
        purchase = PurchaseModel.ON_DEMAND

    provider = await ensure_provider(
        db,
        slug="cyso_cloud",
        name="Cyso Cloud",
        website="https://cyso.cloud",
        type=ProviderType.EU,
    )
    await db.flush()

    region_by_slug = {}
    for reg in CYSOCLOUD_REGIONS:
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
    for flavor_name, (vcpu, ram_gb, price_eur) in CYSOCLOUD_FLAVORS.items():
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
        "Cyso Cloud ingestion completed",
        provider_slug="cyso_cloud",
        regions=len(region_by_slug),
        instance_types=len(CYSOCLOUD_FLAVORS),
        price_rows_inserted=inserted_prices,
    )
