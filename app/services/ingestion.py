"""Idempotent ingestion: upsert providers/regions/instance types, append-only prices."""

from __future__ import annotations

import datetime
from typing import Callable, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EgressRate, InstanceType, OsLicenseRate, Price, Provider, PublicIpRate, Region, StorageRate
from app.models.price import PurchaseModel
from app.models.provider import ProviderType


async def ensure_provider(
    db: AsyncSession,
    slug: str,
    name: str,
    website: str | None = None,
    type: ProviderType = ProviderType.REGIONAL,
    publishes_carbon_emissions: bool = False,
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
        row.publishes_carbon_emissions = publishes_carbon_emissions
        return row
    p = Provider(
        slug=slug,
        name=name,
        website=website,
        type=type,
        publishes_carbon_emissions=publishes_carbon_emissions,
    )
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
    renewable_energy_pct: float | None = None,
    carbon_intensity_g_co2_kwh: float | None = None,
    publishes_carbon_data: bool = False,
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
        if renewable_energy_pct is not None:
            row.renewable_energy_pct = renewable_energy_pct
        if carbon_intensity_g_co2_kwh is not None:
            row.carbon_intensity_g_co2_kwh = carbon_intensity_g_co2_kwh
        row.publishes_carbon_data = publishes_carbon_data
        return row
    r = Region(
        provider_id=provider_id,
        slug=slug,
        name=name,
        country=country,
        is_eu=is_eu,
        renewable_energy_pct=renewable_energy_pct,
        carbon_intensity_g_co2_kwh=carbon_intensity_g_co2_kwh,
        publishes_carbon_data=publishes_carbon_data,
    )
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
    gpu_count: int | None = None,
    gpu_model: str | None = None,
    gpu_memory_gb: float | None = None,
    disk_gb: float | None = None,
    disk_type: str | None = None,
    network_performance: str | None = None,
    cpu_type: str | None = None,
    is_current_gen: bool | None = None,
    transfer_tb: float | None = None,
    is_available: bool | None = None,
    metadata_json: str | None = None,
) -> InstanceType:
    """Get or create instance type by (provider_id, name) (idempotent)."""
    stmt = select(InstanceType).where(InstanceType.provider_id == provider_id).where(InstanceType.name == name).limit(1)
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
        if gpu_count is not None:
            row.gpu_count = gpu_count
        if gpu_model is not None:
            row.gpu_model = gpu_model
        if gpu_memory_gb is not None:
            row.gpu_memory_gb = gpu_memory_gb
        if disk_gb is not None:
            row.disk_gb = disk_gb
        if disk_type is not None:
            row.disk_type = disk_type
        if network_performance is not None:
            row.network_performance = network_performance
        if cpu_type is not None:
            row.cpu_type = cpu_type
        if is_current_gen is not None:
            row.is_current_gen = is_current_gen
        if transfer_tb is not None:
            row.transfer_tb = transfer_tb
        if is_available is not None:
            row.is_available = is_available
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
        gpu_count=gpu_count,
        gpu_model=gpu_model,
        gpu_memory_gb=gpu_memory_gb,
        disk_gb=disk_gb,
        disk_type=disk_type,
        network_performance=network_performance,
        cpu_type=cpu_type,
        is_current_gen=is_current_gen,
        transfer_tb=transfer_tb,
        is_available=is_available if is_available is not None else True,
        metadata_json=metadata_json,
    )
    db.add(it)
    await db.flush()
    return it


async def ensure_egress_rate(
    db: AsyncSession,
    provider_slug: str,
    egress_eur_per_gb: float,
    free_tier_gb_per_month: float = 0.0,
    cross_region_eur_per_gb: float | None = None,
    cross_az_eur_per_gb: float | None = None,
) -> EgressRate:
    """Get or create egress rate by provider_slug (idempotent). Updates existing row."""
    stmt = select(EgressRate).where(EgressRate.provider_slug == provider_slug).limit(1)
    result = await db.execute(stmt)
    row = result.scalars().first()
    if row is not None:
        row.egress_eur_per_gb = egress_eur_per_gb
        row.free_tier_gb_per_month = free_tier_gb_per_month
        row.cross_region_eur_per_gb = cross_region_eur_per_gb
        row.cross_az_eur_per_gb = cross_az_eur_per_gb
        return row
    r = EgressRate(
        provider_slug=provider_slug,
        egress_eur_per_gb=egress_eur_per_gb,
        free_tier_gb_per_month=free_tier_gb_per_month,
        cross_region_eur_per_gb=cross_region_eur_per_gb,
        cross_az_eur_per_gb=cross_az_eur_per_gb,
    )
    db.add(r)
    await db.flush()
    return r


async def ensure_storage_rate(
    db: AsyncSession,
    provider_slug: str,
    storage_type: str,
    eur_per_gb_month: float,
    region_slug: str | None = None,
) -> StorageRate:
    """Get or create storage rate (idempotent). Updates existing row."""
    stmt = (
        select(StorageRate)
        .where(StorageRate.provider_slug == provider_slug)
        .where(StorageRate.storage_type == storage_type)
    )
    if region_slug is not None:
        stmt = stmt.where(StorageRate.region_slug == region_slug)
    else:
        stmt = stmt.where(StorageRate.region_slug.is_(None))
    stmt = stmt.limit(1)
    result = await db.execute(stmt)
    row = result.scalars().first()
    if row is not None:
        row.eur_per_gb_month = eur_per_gb_month
        return row
    r = StorageRate(
        provider_slug=provider_slug,
        region_slug=region_slug,
        storage_type=storage_type,
        eur_per_gb_month=eur_per_gb_month,
    )
    db.add(r)
    await db.flush()
    return r


async def ensure_public_ip_rate(
    db: AsyncSession,
    provider_slug: str,
    eur_per_hour: float,
) -> PublicIpRate:
    """Get or create public IP rate by provider_slug (idempotent)."""
    stmt = select(PublicIpRate).where(PublicIpRate.provider_slug == provider_slug).limit(1)
    result = await db.execute(stmt)
    row = result.scalars().first()
    if row is not None:
        row.eur_per_hour = eur_per_hour
        return row
    r = PublicIpRate(provider_slug=provider_slug, eur_per_hour=eur_per_hour)
    db.add(r)
    await db.flush()
    return r


async def ensure_os_license_rate(
    db: AsyncSession,
    provider_slug: str,
    os_type: str,
    eur_per_vcpu_hour: float,
) -> OsLicenseRate:
    """Get or create OS license rate (idempotent)."""
    stmt = (
        select(OsLicenseRate)
        .where(OsLicenseRate.provider_slug == provider_slug)
        .where(OsLicenseRate.os_type == os_type)
        .limit(1)
    )
    result = await db.execute(stmt)
    row = result.scalars().first()
    if row is not None:
        row.eur_per_vcpu_hour = eur_per_vcpu_hour
        return row
    r = OsLicenseRate(
        provider_slug=provider_slug,
        os_type=os_type,
        eur_per_vcpu_hour=eur_per_vcpu_hour,
    )
    db.add(r)
    await db.flush()
    return r


def _truncate_to_hour(ts: datetime.datetime) -> datetime.datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


T = TypeVar("T")


def pick_one_per_logical_product(
    items: list[T],
    group_key: Callable[[T], tuple],
    sort_key: Callable[[T], tuple] | None = None,
) -> list[T]:
    """Deduplicate items so each logical product (group_key) has exactly one item.

    Avoids multiple meters for the same logical product (e.g. Linux vs Windows,
    different SKUs). For each group, keeps the item with the lowest sort_key.
    If sort_key is None, keeps the first item seen.

    Args:
        items: List of items (e.g. API response items, price records).
        group_key: Function returning (instance_type, region, ...) tuple to group by.
        sort_key: Optional function returning tuple for stable sort (lower = preferred).

    Returns:
        One item per unique group_key.
    """
    groups: dict[tuple, T] = {}
    for item in items:
        key = group_key(item)
        if key not in groups:
            groups[key] = item
        elif sort_key is not None:
            # Keep the one with lower sort_key (e.g. Linux before Windows)
            if sort_key(item) < sort_key(groups[key]):
                groups[key] = item
    return list(groups.values())


async def append_price(
    db: AsyncSession,
    instance_type_id: int,
    region_id: int,
    purchase_model: PurchaseModel,
    currency: str,
    price_per_hour: float,
    price_eur_per_hour: float | None = None,
    source_currency: str | None = None,
    effective_date: datetime.datetime | None = None,
    timestamp: datetime.datetime | None = None,
) -> Price | None:
    """Insert a price row if no duplicate for same (instance_type_id, region_id, purchase_model, hour). Append-only, idempotent per hour."""
    ts = timestamp or datetime.datetime.now(datetime.timezone.utc)
    hour_ts = _truncate_to_hour(ts)
    stmt = (
        select(Price.id)
        .where(
            Price.instance_type_id == instance_type_id,
            Price.region_id == region_id,
            Price.purchase_model == purchase_model,
            Price.timestamp >= hour_ts,
            Price.timestamp < hour_ts + datetime.timedelta(hours=1),
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    if result.scalars().first() is not None:
        return None
    p = Price(
        instance_type_id=instance_type_id,
        region_id=region_id,
        purchase_model=purchase_model,
        currency=currency,
        source_currency=source_currency,
        price_per_hour=price_per_hour,
        price_eur_per_hour=price_eur_per_hour,
        effective_date=effective_date,
        timestamp=ts,
    )
    db.add(p)
    await db.flush()
    return p
