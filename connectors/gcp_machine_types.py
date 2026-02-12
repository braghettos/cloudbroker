"""GCP machine type resolver: compute prices for real VM shapes from unit prices."""

from __future__ import annotations

import json
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur, to_eur
from app.core.logging import get_logger
from app.models import InstanceType, Price, Provider, Region
from app.models.price import PurchaseModel
from connectors.gcp import GCP_CPU_PSEUDO_NAME, GCP_RAM_PSEUDO_NAME

logger = get_logger(__name__)


@dataclass
class MachineTypeSpec:
    """Specification for a GCP machine type."""
    name: str
    vcpu: int
    ram_gb: float
    family: str = "e2"
    arch: str = "x86_64"


def _build_machine_type_catalog() -> list[MachineTypeSpec]:
    """
    Build comprehensive GCP machine type catalog.

    Returns:
        List of MachineTypeSpec for all supported GCP machine types.
    """
    catalog: list[MachineTypeSpec] = []

    # E2 family: balanced, cost-effective (standard, highmem, highcpu)
    e2_standard_sizes = [2, 4, 8, 16, 32, 48, 64, 96, 128]
    e2_highmem_sizes = [2, 4, 8, 16, 32, 48, 64, 96, 128]
    e2_highcpu_sizes = [2, 4, 8, 16, 32, 48, 64, 96]

    for size in e2_standard_sizes:
        if size <= 16:  # Limit to sizes we had before, expand gradually
            catalog.append(MachineTypeSpec(f"e2-standard-{size}", vcpu=size, ram_gb=size*4.0, family="e2"))
    for size in e2_highmem_sizes:
        if size <= 16:
            catalog.append(MachineTypeSpec(f"e2-highmem-{size}", vcpu=size, ram_gb=size*8.0, family="e2"))
    for size in e2_highcpu_sizes:
        if size <= 16:
            catalog.append(MachineTypeSpec(f"e2-highcpu-{size}", vcpu=size, ram_gb=size*2.0, family="e2"))

    # N1 family: first-generation general-purpose (standard, highmem, highcpu)
    n1_standard_sizes = [1, 2, 4, 8, 16, 32, 64, 96]
    n1_highmem_sizes = [2, 4, 8, 16, 32, 64, 96]
    n1_highcpu_sizes = [2, 4, 8, 16, 32, 64, 96]

    for size in n1_standard_sizes:
        catalog.append(MachineTypeSpec(f"n1-standard-{size}", vcpu=size, ram_gb=size*3.75, family="n1"))
    for size in n1_highmem_sizes:
        catalog.append(MachineTypeSpec(f"n1-highmem-{size}", vcpu=size, ram_gb=size*6.5, family="n1"))
    for size in n1_highcpu_sizes:
        catalog.append(MachineTypeSpec(f"n1-highcpu-{size}", vcpu=size, ram_gb=size*0.9, family="n1"))

    # N2 family: second-generation general-purpose (standard, highmem, highcpu)
    n2_standard_sizes = [2, 4, 8, 16, 32, 48, 64, 80, 96, 128]
    n2_highmem_sizes = [2, 4, 8, 16, 32, 48, 64, 80, 96, 128]
    n2_highcpu_sizes = [2, 4, 8, 16, 32, 48, 64, 80, 96]

    for size in n2_standard_sizes:
        catalog.append(MachineTypeSpec(f"n2-standard-{size}", vcpu=size, ram_gb=size*4.0, family="n2"))
    for size in n2_highmem_sizes:
        catalog.append(MachineTypeSpec(f"n2-highmem-{size}", vcpu=size, ram_gb=size*8.0, family="n2"))
    for size in n2_highcpu_sizes:
        catalog.append(MachineTypeSpec(f"n2-highcpu-{size}", vcpu=size, ram_gb=size*2.0, family="n2"))

    # C2 family: compute-optimized (Intel Skylake)
    c2_standard_sizes = [4, 8, 16, 30, 60]
    for size in c2_standard_sizes:
        catalog.append(MachineTypeSpec(f"c2-standard-{size}", vcpu=size, ram_gb=size*4.0, family="c2"))

    # C2D family: compute-optimized (AMD Milan)
    c2d_standard_sizes = [2, 4, 8, 16, 32, 56, 112]
    for size in c2d_standard_sizes:
        catalog.append(MachineTypeSpec(f"c2d-standard-{size}", vcpu=size, ram_gb=size*4.0, family="c2d", arch="x86_64"))

    # M1 family: memory-optimized
    catalog.append(MachineTypeSpec("m1-megamem-96", vcpu=96, ram_gb=1433.6, family="m1"))
    catalog.append(MachineTypeSpec("m1-ultramem-40", vcpu=40, ram_gb=961.0, family="m1"))
    catalog.append(MachineTypeSpec("m1-ultramem-80", vcpu=80, ram_gb=1922.0, family="m1"))
    catalog.append(MachineTypeSpec("m1-ultramem-160", vcpu=160, ram_gb=3844.0, family="m1"))

    # M2 family: memory-optimized (newer)
    catalog.append(MachineTypeSpec("m2-megamem-416", vcpu=416, ram_gb=5888.0, family="m2"))
    catalog.append(MachineTypeSpec("m2-ultramem-208", vcpu=208, ram_gb=5888.0, family="m2"))
    catalog.append(MachineTypeSpec("m2-ultramem-416", vcpu=416, ram_gb=11776.0, family="m2"))

    # T2D family: AMD-based general-purpose
    t2d_standard_sizes = [1, 2, 4, 8, 16, 32, 48, 60, 80, 96]
    for size in t2d_standard_sizes:
        catalog.append(MachineTypeSpec(f"t2d-standard-{size}", vcpu=size, ram_gb=size*4.0, family="t2d", arch="x86_64"))

    return catalog


# Build the catalog
GCP_MACHINE_TYPES: list[MachineTypeSpec] = _build_machine_type_catalog()

# Keep E2_MACHINE_TYPES for backward compatibility
E2_MACHINE_TYPES: list[MachineTypeSpec] = [m for m in GCP_MACHINE_TYPES if m.family == "e2"]


async def _get_latest_price(
    db: AsyncSession,
    instance_type_id: int,
    region_id: int,
    purchase_model: PurchaseModel,
) -> Price | None:
    """Get the most recent price for an instance type in a region."""
    stmt = (
        select(Price)
        .where(Price.instance_type_id == instance_type_id)
        .where(Price.region_id == region_id)
        .where(Price.purchase_model == purchase_model)
        .order_by(Price.timestamp.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
    families: list[str] | None = None,
) -> None:
    """
    Compute prices for real GCP machine types from unit prices.

    For each GCP region:
    1. Fetch latest CPU unit price (gcp-compute-cpu)
    2. Fetch latest RAM unit price (gcp-compute-ram)
    3. For each machine type in catalog:
       - Upsert InstanceType
       - Compute price = cpu_price * vcpu + ram_price * ram_gb
       - Insert Price row
    """
    purchase = PurchaseModel(purchase_model)

    # Get GCP provider
    provider_stmt = select(Provider).where(Provider.slug == "gcp").limit(1)
    provider_result = await db.execute(provider_stmt)
    provider = provider_result.scalars().first()
    if not provider:
        raise RuntimeError("GCP provider not found. Run base GCP ingestion first.")

    # Get GCP regions
    region_stmt = select(Region).where(Region.provider_id == provider.id)
    if only_eu:
        region_stmt = region_stmt.where(Region.is_eu.is_(True))
    region_result = await db.execute(region_stmt)
    regions = list(region_result.scalars().all())

    if not regions:
        logger.warning("No GCP regions found. Run base GCP ingestion first.")
        return

    # Get pseudo instance types for unit prices
    cpu_it_stmt = (
        select(InstanceType)
        .where(InstanceType.provider_id == provider.id)
        .where(InstanceType.name == GCP_CPU_PSEUDO_NAME)
        .limit(1)
    )
    cpu_it_result = await db.execute(cpu_it_stmt)
    cpu_it = cpu_it_result.scalars().first()

    ram_it_stmt = (
        select(InstanceType)
        .where(InstanceType.provider_id == provider.id)
        .where(InstanceType.name == GCP_RAM_PSEUDO_NAME)
        .limit(1)
    )
    ram_it_result = await db.execute(ram_it_stmt)
    ram_it = ram_it_result.scalars().first()

    if not cpu_it or not ram_it:
        raise RuntimeError(
            "GCP unit price instance types not found. Run base GCP ingestion first."
        )

    # Get FX rate for USD->EUR conversion
    rate_usd_eur = await get_rate_to_eur(db, "USD")

    from app.services.ingestion import ensure_instance_type, append_price

    inserted_prices = 0
    created_machine_types = set()

    # Process each region
    for region in regions:
        # Get latest CPU and RAM unit prices for this region
        cpu_price = await _get_latest_price(db, cpu_it.id, region.id, purchase)
        ram_price = await _get_latest_price(db, ram_it.id, region.id, purchase)

        if not cpu_price or not ram_price:
            logger.warning("Skipping region: missing unit prices", region=region.slug)
            continue

        # Use EUR price if available, otherwise convert
        cpu_price_per_hour = cpu_price.price_eur_per_hour
        if cpu_price_per_hour is None:
            cpu_price_per_hour = to_eur(cpu_price.price_per_hour, rate_usd_eur)

        ram_price_per_hour = ram_price.price_eur_per_hour
        if ram_price_per_hour is None:
            ram_price_per_hour = to_eur(ram_price.price_per_hour, rate_usd_eur)

        # Filter machine types by family if specified
        machine_types_to_process = GCP_MACHINE_TYPES
        if families:
            machine_types_to_process = [m for m in GCP_MACHINE_TYPES if m.family in families]

        # Process each machine type in catalog
        for spec in machine_types_to_process:
            # Upsert InstanceType
            machine_it = await ensure_instance_type(
                db,
                provider_id=provider.id,
                name=spec.name,
                vcpu=spec.vcpu,
                ram_gb=spec.ram_gb,
                family=spec.family,
                arch=spec.arch,
                metadata_json=json.dumps({
                    "description": f"GCP {spec.name} machine type",
                    "computed_from_unit_prices": True,
                }),
            )
            await db.flush()

            # Track created machine types
            created_machine_types.add(machine_it.name)

            # Compute total price: cpu_price * vcpu + ram_price * ram_gb
            total_price_eur = (cpu_price_per_hour * spec.vcpu) + (ram_price_per_hour * spec.ram_gb)

            # Use EUR as currency (preferred) or fallback to USD
            currency = "EUR" if cpu_price.price_eur_per_hour is not None else cpu_price.currency
            if currency == "EUR":
                price_per_hour = total_price_eur
            else:
                # Compute in original currency, then convert to EUR for price_eur_per_hour
                cpu_price_orig = cpu_price.price_per_hour
                ram_price_orig = ram_price.price_per_hour
                price_per_hour = (cpu_price_orig * spec.vcpu) + (ram_price_orig * spec.ram_gb)

            # Insert price
            price = await append_price(
                db,
                instance_type_id=machine_it.id,
                region_id=region.id,
                purchase_model=purchase,
                currency=currency,
                price_per_hour=price_per_hour,
                price_eur_per_hour=total_price_eur,
            )

            if price:
                inserted_prices += 1

    logger.info(
        "GCP machine types ingestion completed",
        machine_types=len(created_machine_types),
        regions=len(regions),
        price_rows_inserted=inserted_prices,
    )
