"""Base class for config-based connectors.

Config-based connectors use hardcoded plans and pricing (no API calls required).
This base handles the shared boilerplate: provider creation, region iteration,
instance type upsert, and price insertion.

Subclasses define PROVIDER_*, REGIONS, and implement get_plans() to return
the plan data in a common format.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.price import PurchaseModel
from app.models.provider import ProviderType
from app.services.ingestion import (
    append_price,
    ensure_instance_type,
    ensure_provider,
    ensure_region,
)

logger = get_logger(__name__)


@dataclass
class ConfigPlan:
    """A single instance plan for a config-based connector."""

    name: str
    vcpu: int
    ram_gb: float
    eur_per_hour: float
    family: str = "general-purpose"
    arch: str = "x86_64"
    cpu_type: str | None = None
    is_current_gen: bool | None = True
    disk_gb: float | None = None
    disk_type: str | None = None


@dataclass
class ConfigRegion:
    """A region definition for a config-based connector."""

    slug: str
    name: str
    country: str
    is_eu: bool


class BaseConfigConnector:
    """Base class for config-based connectors.

    Subclasses must define:
        PROVIDER_SLUG: str
        PROVIDER_NAME: str
        PROVIDER_WEBSITE: str
        PROVIDER_TYPE: ProviderType
        REGIONS: list[ConfigRegion]

    And implement:
        get_plans() -> list[ConfigPlan]
    """

    PROVIDER_SLUG: str
    PROVIDER_NAME: str
    PROVIDER_WEBSITE: str
    PROVIDER_TYPE: ProviderType = ProviderType.EU
    REGIONS: list[ConfigRegion]

    @classmethod
    def get_plans(cls) -> list[ConfigPlan]:
        """Return the list of plans to ingest. Override in subclass."""
        raise NotImplementedError

    @classmethod
    async def ingest(
        cls,
        db: AsyncSession,
        only_eu: bool = True,
        purchase_model: str = "on_demand",
    ) -> None:
        """Ingest plans and pricing into database."""
        purchase = PurchaseModel(purchase_model)
        if purchase != PurchaseModel.ON_DEMAND:
            logger.warning(
                "%s only supports on-demand pricing",
                cls.PROVIDER_NAME,
                purchase_model=purchase_model,
            )
            purchase = PurchaseModel.ON_DEMAND

        provider = await ensure_provider(
            db,
            slug=cls.PROVIDER_SLUG,
            name=cls.PROVIDER_NAME,
            website=cls.PROVIDER_WEBSITE,
            type=cls.PROVIDER_TYPE,
        )
        await db.flush()

        regions_to_ingest = [r for r in cls.REGIONS if not only_eu or r.is_eu]
        plans = cls.get_plans()

        inserted_prices = 0
        for reg in regions_to_ingest:
            region = await ensure_region(
                db,
                provider_id=provider.id,
                slug=reg.slug,
                name=reg.name,
                country=reg.country,
                is_eu=reg.is_eu,
            )
            await db.flush()

            for plan in plans:
                it = await ensure_instance_type(
                    db,
                    provider_id=provider.id,
                    name=plan.name,
                    vcpu=plan.vcpu,
                    ram_gb=plan.ram_gb,
                    family=plan.family,
                    arch=plan.arch,
                    cpu_type=plan.cpu_type,
                    is_current_gen=plan.is_current_gen,
                    disk_gb=plan.disk_gb,
                    disk_type=plan.disk_type,
                )
                await db.flush()

                p = await append_price(
                    db,
                    instance_type_id=it.id,
                    region_id=region.id,
                    purchase_model=purchase,
                    currency="EUR",
                    price_per_hour=plan.eur_per_hour,
                    price_eur_per_hour=plan.eur_per_hour,
                )
                if p:
                    inserted_prices += 1

        logger.info(
            "%s ingestion completed",
            cls.PROVIDER_NAME,
            provider_slug=cls.PROVIDER_SLUG,
            regions=len(regions_to_ingest),
            plans=len(plans),
            price_rows_inserted=inserted_prices,
        )
