"""Cherry Servers connector: config-based pricing for virtual server plans.

Cherry Servers is a Lithuanian provider covering the Baltic region (gap in current
EU coverage). Plans and pricing sourced from https://www.cherryservers.com/pricing.

API: https://api.cherryservers.com/v1/ (Bearer token; not required for pricing ingestion)
Pricing: Config-based — update CHERRY_PLANS when provider pricing changes.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.price import PurchaseModel
from app.models.provider import ProviderType
from app.services.ingestion import (
    append_price,
    ensure_instance_type,
    ensure_provider,
    ensure_region,
)
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

CHERRY_REGIONS = [
    {"slug": "eu-lt-1", "name": "Šiauliai (Lithuania)", "country": "LT", "is_eu": True},
    {"slug": "eu-nl-1", "name": "Amsterdam (Netherlands)", "country": "NL", "is_eu": True},
    {"slug": "eu-de-1", "name": "Frankfurt (Germany)",    "country": "DE", "is_eu": True},
    {"slug": "eu-se-1", "name": "Stockholm (Sweden)",     "country": "SE", "is_eu": True},
]

# (vcpu, ram_gb, disk_gb, eur_per_hour)
# Sources: https://www.cherryservers.com/pricing — Virtual Servers section
CHERRY_PLANS: dict[str, tuple[int, float, float, float]] = {
    "virtual-ssd-1":  (1,  1.0,   20.0,  0.015),
    "virtual-ssd-2":  (2,  2.0,   40.0,  0.025),
    "virtual-ssd-4":  (4,  4.0,   80.0,  0.047),
    "virtual-ssd-8":  (8,  8.0,  160.0,  0.094),
    "virtual-ssd-16": (16, 16.0, 320.0,  0.188),
    "virtual-ssd-32": (32, 32.0, 640.0,  0.376),
}


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning("Cherry Servers only supports on-demand pricing", purchase_model=purchase_model)
        purchase = PurchaseModel.ON_DEMAND

    provider = await ensure_provider(
        db,
        slug="cherry_servers",
        name="Cherry Servers",
        website="https://www.cherryservers.com",
        type=ProviderType.EU,
    )
    await db.flush()

    regions_to_ingest = [r for r in CHERRY_REGIONS if not only_eu or r["is_eu"]]

    inserted_prices = 0
    for reg in regions_to_ingest:
        region = await ensure_region(
            db,
            provider_id=provider.id,
            slug=reg["slug"],
            name=reg["name"],
            country=reg["country"],
            is_eu=reg["is_eu"],
        )
        await db.flush()

        for plan_name, (vcpu, ram_gb, disk_gb, eur_per_hour) in CHERRY_PLANS.items():
            it = await ensure_instance_type(
                db,
                provider_id=provider.id,
                name=plan_name,
                vcpu=vcpu,
                ram_gb=ram_gb,
                family="standard",
                arch="x86_64",
                disk_gb=disk_gb,
                disk_type="local_ssd",
            )
            await db.flush()

            p = await append_price(
                db,
                instance_type_id=it.id,
                region_id=region.id,
                purchase_model=purchase,
                currency="EUR",
                price_per_hour=eur_per_hour,
                price_eur_per_hour=eur_per_hour,
            )
            if p:
                inserted_prices += 1

    logger.info(
        "Cherry Servers ingestion completed",
        regions=len(regions_to_ingest),
        plans=len(CHERRY_PLANS),
        price_rows_inserted=inserted_prices,
    )
