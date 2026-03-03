"""Aruba Cloud connector: config-based ingestion (no pricing API)."""

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

# Aruba Cloud region: ITBG-Bergamo (single region, multiple datacenters)
# CloudBroker uses region slug; Aruba API uses location "ITBG-Bergamo" and dataCenter "ITBG-1", "ITBG-2", "ITBG-3"
ARUBA_REGIONS = [
    {"slug": "itbg-bergamo", "name": "Bergamo", "country": "IT", "is_eu": True},
]

# Aruba Cloud Server Linux flavors (from api.arubacloud.com/docs/metadata)
# Flavor name -> (vcpu, ram_gb, price_eur_per_hour)
# Prices from Aruba console / https://www.arubacloud.com/pricing/ (excl. VAT)
ARUBA_FLAVORS = {
    "CSO1A2": (1, 2, 0.0130),
    "CSO1A4": (1, 4, 0.0200),
    "CSO2A4": (2, 4, 0.0260),
    "CSO2A8": (2, 8, 0.03600),
    "CSO4A8": (4, 8, 0.0520),
    "CSO4A16": (4, 16, 0.0740),
    "CSO8A16": (8, 16, 0.0900),
    "CSO8A32": (8, 32, 0.1350),
    "CSO12A24": (12, 24, 0.1950),
    "CSO16A32": (16, 32, 0.2600),
    "CSO16A64": (16, 64, 0.2600),
    "CSO24A48": (24, 48, 0.3800),
    "CSO32A64": (32, 64, 0.3800),
}


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest Aruba Cloud Server flavors and pricing from config (no pricing API)."""
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning(
            "Aruba only supports on-demand pricing, ignoring purchase_model",
            purchase_model=purchase_model,
        )
        purchase = PurchaseModel.ON_DEMAND

    provider = await ensure_provider(
        db,
        slug="aruba",
        name="Aruba Cloud",
        website="https://www.arubacloud.com",
        type=ProviderType.EU,
    )
    await db.flush()

    region_by_slug = {}
    for reg in ARUBA_REGIONS:
        is_eu = reg["is_eu"]
        if only_eu and not is_eu:
            continue
        region = await ensure_region(
            db,
            provider_id=provider.id,
            slug=reg["slug"],
            name=reg["name"],
            country=reg["country"],
            is_eu=is_eu,
        )
        region_by_slug[reg["slug"]] = region
    await db.flush()

    inserted_prices = 0
    for flavor_name, (vcpu, ram_gb, price_eur) in ARUBA_FLAVORS.items():
        it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=flavor_name,
            vcpu=vcpu,
            ram_gb=float(ram_gb),
            family="cloud-server",
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
        "Aruba ingestion completed",
        provider_slug="aruba",
        regions=len(region_by_slug),
        instance_types=len(ARUBA_FLAVORS),
        price_rows_inserted=inserted_prices,
    )
