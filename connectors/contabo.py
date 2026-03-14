"""Contabo connector: config-based pricing for Cloud VPS and Cloud VDS plans.

Contabo is a German provider with exceptional price-to-performance for individual and
developer use. Monthly billing; prices sourced from https://contabo.com/en/pricing/
and converted to hourly (EUR/month ÷ 730).

API: https://api.contabo.com/ (OAuth2 client credentials, not required for ingestion)
Pricing: Config-based — update CONTABO_PLANS when provider pricing changes.
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

# EUR/month ÷ 730 = EUR/hour
_HOURS_PER_MONTH = 730.0

CONTABO_REGIONS = [
    {"slug": "eu-de-1", "name": "Germany (Nuremberg)", "country": "DE", "is_eu": True},
    {"slug": "eu-de-2", "name": "Germany (Munich)", "country": "DE", "is_eu": True},
    {"slug": "eu-gb-1", "name": "UK (London)", "country": "GB", "is_eu": False},
]

# (vcpu, ram_gb, disk_gb, eur_per_month, cpu_type, family)
# Sources: https://contabo.com/en/pricing/ — Cloud VPS and Cloud VDS sections
CONTABO_PLANS: dict[str, tuple[int, float, float, float, str, str]] = {
    # Cloud VPS (shared vCPU, NVMe SSD)
    "cloud-vps-10": (4,  8.0,   75.0,  3.60,  "shared",    "vps"),
    "cloud-vps-20": (6,  12.0,  100.0, 5.60,  "shared",    "vps"),
    "cloud-vps-30": (8,  24.0,  200.0, 11.20, "shared",    "vps"),
    "cloud-vps-40": (12, 48.0,  250.0, 20.00, "shared",    "vps"),
    "cloud-vps-50": (16, 64.0,  300.0, 29.60, "shared",    "vps"),
    "cloud-vps-60": (18, 96.0,  350.0, 39.20, "shared",    "vps"),
    # Cloud VDS (dedicated AMD EPYC physical cores, NVMe SSD)
    "cloud-vds-s":  (3,  24.0,  180.0, 27.52, "dedicated", "vds"),
    "cloud-vds-m":  (4,  32.0,  240.0, 35.84, "dedicated", "vds"),
    "cloud-vds-l":  (6,  48.0,  360.0, 51.20, "dedicated", "vds"),
}


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning("Contabo only supports on-demand pricing", purchase_model=purchase_model)
        purchase = PurchaseModel.ON_DEMAND

    provider = await ensure_provider(
        db,
        slug="contabo",
        name="Contabo",
        website="https://contabo.com",
        type=ProviderType.EU,
    )
    await db.flush()

    regions_to_ingest = [r for r in CONTABO_REGIONS if not only_eu or r["is_eu"]]

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

        for plan_name, (vcpu, ram_gb, disk_gb, eur_per_month, cpu_type, family) in CONTABO_PLANS.items():
            price_eur = round(eur_per_month / _HOURS_PER_MONTH, 6)

            it = await ensure_instance_type(
                db,
                provider_id=provider.id,
                name=plan_name,
                vcpu=vcpu,
                ram_gb=ram_gb,
                family=family,
                arch="x86_64",
                disk_gb=disk_gb,
                disk_type="local_ssd",
                cpu_type=cpu_type,
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
        "Contabo ingestion completed",
        regions=len(regions_to_ingest),
        plans=len(CONTABO_PLANS),
        price_rows_inserted=inserted_prices,
    )
