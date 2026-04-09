"""Candidate fetching and filtering for the recommendation engine.

Builds and executes the SQL query that finds matching (instance_type, region, price)
rows, applying 15+ filter conditions from the RecommendationRequest.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import PROVIDERS_PUBLISH_CARBON
from app.models import InstanceType, Price, Provider, ProviderType, Region
from app.models.price import PurchaseModel
from app.schemas.recommendations import RecommendationRequest
from app.services.sustainability_service import get_current_sustainability


@dataclass
class Candidate:
    """One (instance_type, region, price) row from the DB."""

    instance_type_id: int
    instance_type_name: str
    provider_slug: str
    provider_type: ProviderType
    region_id: int
    region_slug: str
    vcpu: int
    ram_gb: float
    arch: str | None
    family: str | None
    gpu_count: int | None
    gpu_model: str | None = None
    price_per_hour: float = 0.0
    price_currency: str = "EUR"
    region_is_eu: bool = False
    renewable_energy_pct: float | None = None
    carbon_intensity_g_co2_kwh: float | None = None


async def fetch_candidates(db: AsyncSession, req: RecommendationRequest) -> list[Candidate]:
    """Load (instance_type, region, latest price) matching filters."""
    arch_val = req.arch

    try:
        purchase_model = PurchaseModel(req.purchase_model.lower())
    except ValueError:
        purchase_model = PurchaseModel.ON_DEMAND

    # Subquery: latest price per (instance_type_id, region_id) for requested purchase_model
    latest = (
        select(
            Price.instance_type_id,
            Price.region_id,
            func.max(Price.timestamp).label("max_ts"),
        )
        .where(Price.purchase_model == purchase_model)
        .group_by(Price.instance_type_id, Price.region_id)
    )
    latest_subq = latest.subquery()

    effective_price = func.coalesce(Price.price_eur_per_hour, Price.price_per_hour)

    stmt = (
        select(
            InstanceType.id.label("instance_type_id"),
            InstanceType.name.label("instance_type_name"),
            Provider.slug.label("provider_slug"),
            Provider.type.label("provider_type"),
            Region.id.label("region_id"),
            Region.slug.label("region_slug"),
            InstanceType.vcpu,
            InstanceType.ram_gb,
            InstanceType.arch,
            InstanceType.family,
            InstanceType.gpu_count,
            InstanceType.gpu_model,
            InstanceType.is_available,
            effective_price.label("price_per_hour"),
            Price.currency.label("price_currency"),
            Region.is_eu.label("region_is_eu"),
        )
        .select_from(InstanceType)
        .join(Price, InstanceType.id == Price.instance_type_id)
        .join(
            latest_subq,
            and_(
                Price.instance_type_id == latest_subq.c.instance_type_id,
                Price.region_id == latest_subq.c.region_id,
                Price.timestamp == latest_subq.c.max_ts,
            ),
        )
        .join(Region, Price.region_id == Region.id)
        .join(Provider, InstanceType.provider_id == Provider.id)
        .where(InstanceType.vcpu >= req.min_vcpu)
        .where(InstanceType.ram_gb >= req.min_ram_gb)
        .where(InstanceType.arch == arch_val)
        .where(Price.purchase_model == purchase_model)
        .where(effective_price <= req.max_price_eur_per_hour)
    )

    if req.region_constraint.value == "EU":
        stmt = stmt.where(Region.is_eu.is_(True))

    if req.allowed_providers:
        stmt = stmt.where(Provider.slug.in_(req.allowed_providers))

    if req.excluded_providers:
        stmt = stmt.where(~Provider.slug.in_(req.excluded_providers))

    if req.providers_publish_carbon_only:
        stmt = stmt.where(Provider.slug.in_(PROVIDERS_PUBLISH_CARBON))

    if req.excluded_instance_types:
        stmt = stmt.where(~InstanceType.name.in_(req.excluded_instance_types))

    if req.excluded_instance_type_prefixes:
        for prefix in req.excluded_instance_type_prefixes:
            pattern = f"{prefix}%"
            stmt = stmt.where(~InstanceType.name.ilike(pattern))

    # Azure ARM-only sizes may be mislabeled as x86_64; exclude when requesting x86_64
    if arch_val == "x86_64":
        stmt = stmt.where(
            or_(
                Provider.slug != "azure",
                and_(
                    ~InstanceType.name.ilike("%pls%"),
                    ~InstanceType.name.ilike("%ps_v5%"),
                    ~InstanceType.name.ilike("%plds%"),
                    ~InstanceType.name.ilike("%pts%"),
                ),
            )
        )

    if req.min_gpu_count is not None and req.min_gpu_count > 0:
        stmt = stmt.where(
            InstanceType.gpu_count.isnot(None),
            InstanceType.gpu_count >= req.min_gpu_count,
        )

    if req.gpu_type:
        gpu_pattern = f"%{req.gpu_type}%"
        stmt = stmt.where(
            or_(
                InstanceType.name.ilike(gpu_pattern),
                InstanceType.gpu_model.ilike(gpu_pattern),
            )
        )

    if req.min_disk_gb is not None and req.min_disk_gb > 0:
        stmt = stmt.where(
            InstanceType.disk_gb.isnot(None),
            InstanceType.disk_gb >= req.min_disk_gb,
        )

    if req.current_gen_only:
        stmt = stmt.where(
            or_(
                InstanceType.is_current_gen.is_(None),
                InstanceType.is_current_gen.is_(True),
            )
        )

    if req.min_transfer_tb is not None and req.min_transfer_tb > 0:
        stmt = stmt.where(
            InstanceType.transfer_tb.isnot(None),
            InstanceType.transfer_tb >= req.min_transfer_tb,
        )

    if req.cpu_type:
        stmt = stmt.where(
            or_(
                InstanceType.cpu_type.is_(None),
                InstanceType.cpu_type.ilike(f"%{req.cpu_type}%"),
            )
        )

    if req.network_performance:
        stmt = stmt.where(
            or_(
                InstanceType.network_performance.is_(None),
                InstanceType.network_performance.ilike(f"%{req.network_performance}%"),
            )
        )

    stmt = stmt.where(or_(InstanceType.is_available.is_(None), InstanceType.is_available.is_(True)))

    result = await db.execute(stmt)
    rows = result.all()

    return [
        Candidate(
            instance_type_id=r.instance_type_id,
            instance_type_name=r.instance_type_name,
            provider_slug=r.provider_slug,
            provider_type=r.provider_type,
            region_id=r.region_id,
            region_slug=r.region_slug,
            vcpu=r.vcpu,
            ram_gb=r.ram_gb,
            arch=r.arch,
            family=r.family,
            gpu_count=r.gpu_count,
            gpu_model=r.gpu_model,
            price_per_hour=r.price_per_hour,
            price_currency=r.price_currency or "EUR",
            region_is_eu=r.region_is_eu,
        )
        for r in rows
    ]


async def enrich_and_filter_sustainability(
    db: AsyncSession,
    candidates: list[Candidate],
    req: RecommendationRequest,
) -> list[Candidate]:
    """Fetch sustainability (ENTSO-E for EU, config), fallback to DB."""
    seen: set[tuple[str, str]] = set()
    region_sustainability: dict[tuple[str, str], tuple[float | None, float | None]] = {}

    async def fetch_one(provider_slug: str, region_slug: str) -> None:
        key = (provider_slug, region_slug)
        if key in seen:
            return
        seen.add(key)
        data = await get_current_sustainability(provider_slug, region_slug)
        if data:
            region_sustainability[key] = (
                data.renewable_energy_pct,
                data.carbon_intensity_g_co2_kwh,
            )
        else:
            region_sustainability[key] = (None, None)

    await asyncio.gather(
        *[fetch_one(c.provider_slug, c.region_slug) for c in candidates],
        return_exceptions=True,
    )

    # DB fallback for regions where real-time returned None
    missing = [k for k, v in region_sustainability.items() if v == (None, None)]
    if missing:
        stmt = (
            select(
                Provider.slug.label("provider_slug"),
                Region.slug.label("region_slug"),
                Region.renewable_energy_pct,
                Region.carbon_intensity_g_co2_kwh,
            )
            .join(Region, Region.provider_id == Provider.id)
            .where(tuple_(Provider.slug, Region.slug).in_(missing))
        )
        result = await db.execute(stmt)
        for row in result.all():
            key = (row.provider_slug, row.region_slug)
            if (
                row.renewable_energy_pct is not None or row.carbon_intensity_g_co2_kwh is not None
            ) and key in region_sustainability:
                region_sustainability[key] = (row.renewable_energy_pct, row.carbon_intensity_g_co2_kwh)

    enriched: list[Candidate] = []
    for c in candidates:
        key = (c.provider_slug, c.region_slug)
        renewable, carbon = region_sustainability.get(key, (None, None))
        c.renewable_energy_pct = renewable
        c.carbon_intensity_g_co2_kwh = carbon

        if req.min_renewable_pct is not None and req.min_renewable_pct > 0:
            if renewable is None or renewable < req.min_renewable_pct:
                continue
        if req.max_carbon_intensity is not None:
            if carbon is not None and carbon > req.max_carbon_intensity:
                continue

        enriched.append(c)

    return enriched
