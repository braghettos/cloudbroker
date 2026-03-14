"""Clouding.io connector: config-based pricing for Spanish cloud provider.

Clouding.io is a Barcelona-based provider operating from a Tier IV data center.
Fills the Iberian Peninsula gap in EU coverage. Ultra-affordable hourly billing
with no minimum commitments.

API: https://api.clouding.io (REST API available; pricing from public pricing page)
Pricing: Config-based — update CLOUDING_PLANS when provider pricing changes.
Sources: https://clouding.io/en/ and https://www.vpsbenchmarks.com/hosters/clouding_io
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

CLOUDING_REGIONS = [
    {"slug": "es-bcn-1", "name": "Barcelona (Tier IV)", "country": "ES", "is_eu": True},
]

# (vcpu_cores, ram_gb, disk_gb, eur_per_hour)
# Sources: https://clouding.io/en/pricing/ — standard flexible plans
# Note: Clouding.io vCores are fractional for entry plans; we round up to 1 minimum.
CLOUDING_PLANS: dict[str, tuple[int, float, float, float]] = {
    # Entry / micro plans
    "micro-1":   (1,  1.0,   5.0,  0.004),
    "micro-2":   (1,  2.0,  30.0,  0.011),
    # Small plans
    "small-1":   (2,  4.0,  60.0,  0.022),
    "small-2":   (4,  8.0, 100.0,  0.045),
    # Medium plans
    "medium-1":  (6,  16.0, 200.0, 0.090),
    "medium-2":  (8,  32.0, 400.0, 0.180),
    # Large plans
    "large-1":   (12, 48.0, 600.0, 0.270),
    "large-2":   (16, 64.0, 800.0, 0.360),
}


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning("Clouding.io only supports on-demand pricing", purchase_model=purchase_model)
        purchase = PurchaseModel.ON_DEMAND

    provider = await ensure_provider(
        db,
        slug="clouding",
        name="Clouding.io",
        website="https://clouding.io",
        type=ProviderType.EU,
    )
    await db.flush()

    regions_to_ingest = [r for r in CLOUDING_REGIONS if not only_eu or r["is_eu"]]

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

        for plan_name, (vcpu, ram_gb, disk_gb, eur_per_hour) in CLOUDING_PLANS.items():
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
        "Clouding.io ingestion completed",
        regions=len(regions_to_ingest),
        plans=len(CLOUDING_PLANS),
        price_rows_inserted=inserted_prices,
    )
