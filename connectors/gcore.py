"""Gcore Cloud connector: per-resource config-based pricing for EU regions.

Gcore is a Luxembourg-headquartered provider with 9+ European locations and competitive
pricing. Uses per-minute billing; rates sourced from https://gcore.com/pricing/cloud.

API: https://api.gcore.com/cloud (public pricing endpoints available)
Pricing: Config-based per-resource model — update rates when provider pricing changes.
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

GCORE_REGIONS = [
    {"slug": "lux-1",    "name": "Luxembourg 1",  "country": "LU", "is_eu": True},
    {"slug": "lux-2",    "name": "Luxembourg 2",  "country": "LU", "is_eu": True},
    {"slug": "ams-1",    "name": "Amsterdam",     "country": "NL", "is_eu": True},
    {"slug": "fra-1",    "name": "Frankfurt",     "country": "DE", "is_eu": True},
    {"slug": "darm-1",   "name": "Darmstadt",     "country": "DE", "is_eu": True},
    {"slug": "par-2",    "name": "Paris",         "country": "FR", "is_eu": True},
    {"slug": "waw-1",    "name": "Warsaw",        "country": "PL", "is_eu": True},
    {"slug": "hel-1",    "name": "Helsinki",      "country": "FI", "is_eu": True},
    {"slug": "lon-1",    "name": "London",        "country": "GB", "is_eu": False},
]

# Standard vCPU/RAM combinations — Gcore bills per-resource per minute
STANDARD_CONFIGS: list[tuple[int, float]] = [
    (1, 2), (1, 4),
    (2, 4), (2, 8),
    (4, 8), (4, 16),
    (8, 16), (8, 32),
    (16, 32), (16, 64),
]

# Per-resource rates in EUR/hr sourced from https://gcore.com/pricing/cloud
# vCPU: ~€0.0066/hr, RAM: ~€0.0033/GB/hr (Luxembourg baseline, other EU regions similar)
GCORE_EUR_PER_VCPU_HR: float = 0.0066
GCORE_EUR_PER_GB_RAM_HR: float = 0.0033


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning("Gcore only supports on-demand pricing", purchase_model=purchase_model)
        purchase = PurchaseModel.ON_DEMAND

    provider = await ensure_provider(
        db,
        slug="gcore",
        name="Gcore",
        website="https://gcore.com/cloud",
        type=ProviderType.EU,
    )
    await db.flush()

    regions_to_ingest = [r for r in GCORE_REGIONS if not only_eu or r["is_eu"]]

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

        for vcpu, ram_gb in STANDARD_CONFIGS:
            price_eur = round(
                vcpu * GCORE_EUR_PER_VCPU_HR + ram_gb * GCORE_EUR_PER_GB_RAM_HR, 6
            )
            name = f"g1-standard-{vcpu}-{int(ram_gb)}"

            it = await ensure_instance_type(
                db,
                provider_id=provider.id,
                name=name,
                vcpu=vcpu,
                ram_gb=float(ram_gb),
                family="standard",
                arch="x86_64",
            )
            await db.flush()

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
        "Gcore ingestion completed",
        regions=len(regions_to_ingest),
        configurations=len(STANDARD_CONFIGS),
        price_rows_inserted=inserted_prices,
        eur_per_vcpu_hr=GCORE_EUR_PER_VCPU_HR,
        eur_per_gb_ram_hr=GCORE_EUR_PER_GB_RAM_HR,
    )
