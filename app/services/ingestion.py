"""Idempotent ingestion: upsert providers/regions/instance types, append-only prices."""

from __future__ import annotations

import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InstanceType, Price, Provider, Region
from app.models.price import PurchaseModel
from app.models.provider import ProviderType


async def ensure_provider(
    db: AsyncSession,
    slug: str,
    name: str,
    website: str | None = None,
    type: ProviderType = ProviderType.REGIONAL,
) -> Provider:
    """Get or create provider by slug (idempotent)."""
    stmt = select(Provider).where(Provider.slug == slug).limit(1)
    result = await db.execute(stmt)
    row = result.scalars().first()
    if row is not None:
        row.name = name
        if website is not None:
            row.website = website
        row.type = type
        return row
    p = Provider(slug=slug, name=name, website=website, type=type)
    db.add(p)
    await db.flush()
    return p


async def ensure_region(
    db: AsyncSession,
    provider_id: int,
    slug: str,
    name: str,
    country: str | None = None,
    is_eu: bool = False,
) -> Region:
    """Get or create region by (provider_id, slug) (idempotent)."""
    stmt = select(Region).where(Region.provider_id == provider_id).where(Region.slug == slug).limit(1)
    result = await db.execute(stmt)
    row = result.scalars().first()
    if row is not None:
        row.name = name
        if country is not None:
            row.country = country
        row.is_eu = is_eu
        return row
    r = Region(provider_id=provider_id, slug=slug, name=name, country=country, is_eu=is_eu)
    db.add(r)
    await db.flush()
    return r


async def ensure_instance_type(
    db: AsyncSession,
    provider_id: int,
    name: str,
    vcpu: int,
    ram_gb: float,
    family: str | None = None,
    generation: str | None = None,
    arch: str | None = None,
    metadata_json: str | None = None,
) -> InstanceType:
    """Get or create instance type by (provider_id, name) (idempotent)."""
    stmt = (
        select(InstanceType)
        .where(InstanceType.provider_id == provider_id)
        .where(InstanceType.name == name)
        .limit(1)
    )
    result = await db.execute(stmt)
    row = result.scalars().first()
    if row is not None:
        row.vcpu = vcpu
        row.ram_gb = ram_gb
        if family is not None:
            row.family = family
        if generation is not None:
            row.generation = generation
        if arch is not None:
            row.arch = arch
        if metadata_json is not None:
            row.metadata_json = metadata_json
        return row
    it = InstanceType(
        provider_id=provider_id,
        name=name,
        vcpu=vcpu,
        ram_gb=ram_gb,
        family=family,
        generation=generation,
        arch=arch,
        metadata_json=metadata_json,
    )
    db.add(it)
    await db.flush()
    return it


def _truncate_to_hour(ts: datetime.datetime) -> datetime.datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


async def append_price(
    db: AsyncSession,
    instance_type_id: int,
    region_id: int,
    purchase_model: PurchaseModel,
    currency: str,
    price_per_hour: float,
    price_eur_per_hour: float | None = None,
    timestamp: datetime.datetime | None = None,
) -> Price | None:
    """Insert a price row if no duplicate for same (instance_type_id, region_id, purchase_model, hour). Append-only, idempotent per hour."""
    ts = timestamp or datetime.datetime.now(datetime.timezone.utc)
    hour_ts = _truncate_to_hour(ts)
    stmt = select(Price.id).where(
        Price.instance_type_id == instance_type_id,
        Price.region_id == region_id,
        Price.purchase_model == purchase_model,
        Price.timestamp >= hour_ts,
        Price.timestamp < hour_ts + datetime.timedelta(hours=1),
    ).limit(1)
    result = await db.execute(stmt)
    if result.scalars().first() is not None:
        return None
    p = Price(
        instance_type_id=instance_type_id,
        region_id=region_id,
        purchase_model=purchase_model,
        currency=currency,
        price_per_hour=price_per_hour,
        price_eur_per_hour=price_eur_per_hour,
        timestamp=ts,
    )
    db.add(p)
    await db.flush()
    return p
