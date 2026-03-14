"""GCP machine type resolver: compute prices for real VM shapes from unit prices.

All machine types are fetched live from the GCP Compute Engine Machine Types API.
Requires:
  - google-cloud-compute package (`pip install google-cloud-compute`)
  - Application Default Credentials (run `gcloud auth application-default login`)
    OR set GOOGLE_APPLICATION_CREDENTIALS to a service account key file path
  - GOOGLE_CLOUD_PROJECT environment variable set to your GCP project ID
"""

from __future__ import annotations

import asyncio
import json
import os
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
    gpu_count: int | None = None
    gpu_model: str | None = None
    is_current_gen: bool | None = None


def _build_machine_type_catalog() -> list[MachineTypeSpec]:  # noqa: C901
    """DEPRECATED: This static catalog is no longer used.

    All machine types are fetched from the GCP Compute Engine API.
    This function is kept only to avoid breaking any external references.
    Returns an empty list.
    """
    return []


def _build_machine_type_catalog_legacy() -> list[MachineTypeSpec]:
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
            catalog.append(MachineTypeSpec(f"e2-standard-{size}", vcpu=size, ram_gb=size * 4.0, family="e2"))
    for size in e2_highmem_sizes:
        if size <= 16:
            catalog.append(MachineTypeSpec(f"e2-highmem-{size}", vcpu=size, ram_gb=size * 8.0, family="e2"))
    for size in e2_highcpu_sizes:
        if size <= 16:
            catalog.append(MachineTypeSpec(f"e2-highcpu-{size}", vcpu=size, ram_gb=size * 2.0, family="e2"))

    # N1 family: first-generation general-purpose (standard, highmem, highcpu)
    n1_standard_sizes = [1, 2, 4, 8, 16, 32, 64, 96]
    n1_highmem_sizes = [2, 4, 8, 16, 32, 64, 96]
    n1_highcpu_sizes = [2, 4, 8, 16, 32, 64, 96]

    for size in n1_standard_sizes:
        catalog.append(MachineTypeSpec(f"n1-standard-{size}", vcpu=size, ram_gb=size * 3.75, family="n1"))
    for size in n1_highmem_sizes:
        catalog.append(MachineTypeSpec(f"n1-highmem-{size}", vcpu=size, ram_gb=size * 6.5, family="n1"))
    for size in n1_highcpu_sizes:
        catalog.append(MachineTypeSpec(f"n1-highcpu-{size}", vcpu=size, ram_gb=size * 0.9, family="n1"))

    # N2 family: second-generation general-purpose (standard, highmem, highcpu)
    n2_standard_sizes = [2, 4, 8, 16, 32, 48, 64, 80, 96, 128]
    n2_highmem_sizes = [2, 4, 8, 16, 32, 48, 64, 80, 96, 128]
    n2_highcpu_sizes = [2, 4, 8, 16, 32, 48, 64, 80, 96]

    for size in n2_standard_sizes:
        catalog.append(MachineTypeSpec(f"n2-standard-{size}", vcpu=size, ram_gb=size * 4.0, family="n2"))
    for size in n2_highmem_sizes:
        catalog.append(MachineTypeSpec(f"n2-highmem-{size}", vcpu=size, ram_gb=size * 8.0, family="n2"))
    for size in n2_highcpu_sizes:
        catalog.append(MachineTypeSpec(f"n2-highcpu-{size}", vcpu=size, ram_gb=size * 2.0, family="n2"))

    # C2 family: compute-optimized (Intel Skylake)
    c2_standard_sizes = [4, 8, 16, 30, 60]
    for size in c2_standard_sizes:
        catalog.append(MachineTypeSpec(f"c2-standard-{size}", vcpu=size, ram_gb=size * 4.0, family="c2"))

    # C2D family: compute-optimized (AMD Milan)
    c2d_standard_sizes = [2, 4, 8, 16, 32, 56, 112]
    for size in c2d_standard_sizes:
        catalog.append(
            MachineTypeSpec(f"c2d-standard-{size}", vcpu=size, ram_gb=size * 4.0, family="c2d", arch="x86_64")
        )

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
        catalog.append(
            MachineTypeSpec(f"t2d-standard-{size}", vcpu=size, ram_gb=size * 4.0, family="t2d", arch="x86_64")
        )

    # T2A family: Arm-based general-purpose (Ampere Altra)
    t2a_standard_sizes = [1, 2, 4, 8, 16, 32, 48]
    for size in t2a_standard_sizes:
        catalog.append(
            MachineTypeSpec(f"t2a-standard-{size}", vcpu=size, ram_gb=size * 4.0, family="t2a", arch="arm64")
        )

    # N2D family: AMD EPYC-based
    n2d_standard_sizes = [2, 4, 8, 16, 32, 48, 64, 80, 96, 128, 224]
    n2d_highmem_sizes = [2, 4, 8, 16, 32, 48, 64, 80, 96]
    n2d_highcpu_sizes = [2, 4, 8, 16, 32, 48, 64, 80, 96, 128, 224]
    for size in n2d_standard_sizes:
        catalog.append(MachineTypeSpec(f"n2d-standard-{size}", vcpu=size, ram_gb=size * 4.0, family="n2d"))
    for size in n2d_highmem_sizes:
        catalog.append(MachineTypeSpec(f"n2d-highmem-{size}", vcpu=size, ram_gb=size * 8.0, family="n2d"))
    for size in n2d_highcpu_sizes:
        catalog.append(MachineTypeSpec(f"n2d-highcpu-{size}", vcpu=size, ram_gb=size * 2.0, family="n2d"))

    # C3 family: Intel Sapphire Rapids (current gen compute-optimized)
    c3_standard_sizes = [4, 8, 22, 44, 88, 176]
    c3_highmem_sizes = [4, 8, 22, 44, 88, 176]
    c3_highcpu_sizes = [4, 8, 22, 44, 88, 176]
    for size in c3_standard_sizes:
        catalog.append(MachineTypeSpec(f"c3-standard-{size}", vcpu=size, ram_gb=size * 4.0, family="c3", is_current_gen=True))
    for size in c3_highmem_sizes:
        catalog.append(MachineTypeSpec(f"c3-highmem-{size}", vcpu=size, ram_gb=size * 8.0, family="c3", is_current_gen=True))
    for size in c3_highcpu_sizes:
        catalog.append(MachineTypeSpec(f"c3-highcpu-{size}", vcpu=size, ram_gb=size * 2.0, family="c3", is_current_gen=True))

    # C3D family: AMD EPYC Genoa (current gen)
    c3d_standard_sizes = [4, 8, 16, 30, 60, 90, 180, 360]
    for size in c3d_standard_sizes:
        catalog.append(MachineTypeSpec(f"c3d-standard-{size}", vcpu=size, ram_gb=size * 4.0, family="c3d", is_current_gen=True))

    # N4 family: Intel Emerald Rapids (current gen)
    n4_standard_sizes = [2, 4, 8, 16, 32, 48, 64, 80]
    n4_highmem_sizes = [2, 4, 8, 16, 32, 48, 64, 80]
    n4_highcpu_sizes = [2, 4, 8, 16, 32, 48, 64, 80]
    for size in n4_standard_sizes:
        catalog.append(MachineTypeSpec(f"n4-standard-{size}", vcpu=size, ram_gb=size * 4.0, family="n4", is_current_gen=True))
    for size in n4_highmem_sizes:
        catalog.append(MachineTypeSpec(f"n4-highmem-{size}", vcpu=size, ram_gb=size * 8.0, family="n4", is_current_gen=True))
    for size in n4_highcpu_sizes:
        catalog.append(MachineTypeSpec(f"n4-highcpu-{size}", vcpu=size, ram_gb=size * 2.0, family="n4", is_current_gen=True))

    # A2 family: NVIDIA A100 GPUs
    catalog.append(MachineTypeSpec("a2-highgpu-1g", vcpu=12, ram_gb=85.0, family="a2", gpu_count=1, gpu_model="nvidia-a100-80gb"))
    catalog.append(MachineTypeSpec("a2-highgpu-2g", vcpu=24, ram_gb=170.0, family="a2", gpu_count=2, gpu_model="nvidia-a100-80gb"))
    catalog.append(MachineTypeSpec("a2-highgpu-4g", vcpu=48, ram_gb=340.0, family="a2", gpu_count=4, gpu_model="nvidia-a100-80gb"))
    catalog.append(MachineTypeSpec("a2-highgpu-8g", vcpu=96, ram_gb=680.0, family="a2", gpu_count=8, gpu_model="nvidia-a100-80gb"))
    catalog.append(MachineTypeSpec("a2-megagpu-16g", vcpu=96, ram_gb=1360.0, family="a2", gpu_count=16, gpu_model="nvidia-a100-80gb"))
    catalog.append(MachineTypeSpec("a2-ultragpu-1g", vcpu=12, ram_gb=170.0, family="a2", gpu_count=1, gpu_model="nvidia-a100-80gb"))
    catalog.append(MachineTypeSpec("a2-ultragpu-2g", vcpu=24, ram_gb=340.0, family="a2", gpu_count=2, gpu_model="nvidia-a100-80gb"))
    catalog.append(MachineTypeSpec("a2-ultragpu-4g", vcpu=48, ram_gb=680.0, family="a2", gpu_count=4, gpu_model="nvidia-a100-80gb"))
    catalog.append(MachineTypeSpec("a2-ultragpu-8g", vcpu=96, ram_gb=1360.0, family="a2", gpu_count=8, gpu_model="nvidia-a100-80gb"))

    # A3 family: NVIDIA H100 GPUs (current gen)
    catalog.append(MachineTypeSpec("a3-highgpu-1g", vcpu=26, ram_gb=234.0, family="a3", gpu_count=1, gpu_model="nvidia-h100-80gb", is_current_gen=True))
    catalog.append(MachineTypeSpec("a3-highgpu-2g", vcpu=52, ram_gb=468.0, family="a3", gpu_count=2, gpu_model="nvidia-h100-80gb", is_current_gen=True))
    catalog.append(MachineTypeSpec("a3-highgpu-4g", vcpu=104, ram_gb=936.0, family="a3", gpu_count=4, gpu_model="nvidia-h100-80gb", is_current_gen=True))
    catalog.append(MachineTypeSpec("a3-highgpu-8g", vcpu=208, ram_gb=1872.0, family="a3", gpu_count=8, gpu_model="nvidia-h100-80gb", is_current_gen=True))
    catalog.append(MachineTypeSpec("a3-megagpu-8g", vcpu=208, ram_gb=1872.0, family="a3", gpu_count=8, gpu_model="nvidia-h100-mega-80gb", is_current_gen=True))

    # G2 family: NVIDIA L4 GPUs
    catalog.append(MachineTypeSpec("g2-standard-4", vcpu=4, ram_gb=16.0, family="g2", gpu_count=1, gpu_model="nvidia-l4"))
    catalog.append(MachineTypeSpec("g2-standard-8", vcpu=8, ram_gb=32.0, family="g2", gpu_count=1, gpu_model="nvidia-l4"))
    catalog.append(MachineTypeSpec("g2-standard-12", vcpu=12, ram_gb=48.0, family="g2", gpu_count=1, gpu_model="nvidia-l4"))
    catalog.append(MachineTypeSpec("g2-standard-16", vcpu=16, ram_gb=64.0, family="g2", gpu_count=1, gpu_model="nvidia-l4"))
    catalog.append(MachineTypeSpec("g2-standard-24", vcpu=24, ram_gb=96.0, family="g2", gpu_count=2, gpu_model="nvidia-l4"))
    catalog.append(MachineTypeSpec("g2-standard-32", vcpu=32, ram_gb=128.0, family="g2", gpu_count=1, gpu_model="nvidia-l4"))
    catalog.append(MachineTypeSpec("g2-standard-48", vcpu=48, ram_gb=192.0, family="g2", gpu_count=4, gpu_model="nvidia-l4"))
    catalog.append(MachineTypeSpec("g2-standard-96", vcpu=96, ram_gb=384.0, family="g2", gpu_count=8, gpu_model="nvidia-l4"))

    return catalog


# Legacy static catalog for gcp_verify (machine types not fetched from API)
GCP_MACHINE_TYPES: list[MachineTypeSpec] = _build_machine_type_catalog_legacy()


def _fetch_compute_machine_types_sync(project_id: str, zone: str) -> list[MachineTypeSpec]:
    """Fetch machine types from GCP Compute Engine API for a zone (synchronous).

    Raises RuntimeError if google-cloud-compute is not installed.
    Returns empty list if API call fails for the specific zone.
    """
    try:
        from google.cloud import compute_v1  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "google-cloud-compute is not installed. "
            "Install it with: pip install google-cloud-compute"
        ) from exc

    try:
        client = compute_v1.MachineTypesClient()
        request = compute_v1.ListMachineTypesRequest(project=project_id, zone=zone)
        page_result = client.list(request=request)
        specs = []
        for machine_type in page_result:
            name = machine_type.name or ""
            if not name:
                continue
            vcpu = machine_type.guest_cpus or 0
            ram_mb = machine_type.memory_mb or 0
            ram_gb = ram_mb / 1024.0
            if vcpu <= 0 or ram_gb <= 0:
                continue
            parts = name.split("-")
            family = parts[0] if parts else "unknown"
            arch = "arm64" if family in ("t2a",) else "x86_64"
            gpu_count: int | None = None
            gpu_model: str | None = None
            accel = getattr(machine_type, "accelerators", None)
            if accel:
                for acc in accel:
                    count = getattr(acc, "guest_accelerator_count", 0) or 0
                    model = getattr(acc, "guest_accelerator_type", "") or ""
                    if count > 0:
                        gpu_count = (gpu_count or 0) + count
                        gpu_model = model or gpu_model
            specs.append(MachineTypeSpec(
                name=name,
                vcpu=vcpu,
                ram_gb=ram_gb,
                family=family,
                arch=arch,
                gpu_count=gpu_count,
                gpu_model=gpu_model,
                is_current_gen=family not in ("n1", "f1", "g1"),
            ))
        return specs
    except RuntimeError:
        raise
    except Exception as e:
        logger.warning("GCP Compute Engine Machine Types API zone failed", zone=zone, error=str(e))
        return []


async def _load_live_machine_types(only_eu: bool) -> list[MachineTypeSpec]:
    """Fetch live machine types from GCP Compute Engine API.

    Requires:
      - GOOGLE_CLOUD_PROJECT environment variable
      - google-cloud-compute package
      - Application Default Credentials (ADC) or GOOGLE_APPLICATION_CREDENTIALS

    Raises RuntimeError if credentials or GOOGLE_CLOUD_PROJECT are missing.
    """
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT_ID") or ""
    if not project_id:
        raise RuntimeError(
            "GCP Compute Engine API requires GOOGLE_CLOUD_PROJECT environment variable. "
            "Set it to your GCP project ID."
        )

    eu_zones = [
        "europe-west1-b", "europe-west2-a", "europe-west3-a",
        "europe-west4-a", "europe-north1-a", "europe-west6-a",
        "europe-west9-a", "europe-southwest1-a", "europe-central2-a",
    ]
    global_zones = eu_zones + [
        "us-central1-a", "us-east1-b", "us-east4-a", "us-west1-a", "us-west2-a",
        "us-west3-a", "us-west4-a", "northamerica-northeast1-a",
        "southamerica-east1-a", "asia-east1-a", "asia-east2-a",
        "asia-northeast1-a", "asia-northeast3-a", "asia-southeast1-a",
        "asia-southeast2-a", "asia-south1-a", "australia-southeast1-a",
        "me-west1-a", "me-central1-a", "africa-south1-a",
    ]
    zones_to_check = eu_zones if only_eu else global_zones

    loop = asyncio.get_event_loop()
    all_specs: dict[str, MachineTypeSpec] = {}

    for zone in zones_to_check:
        zone_specs = await loop.run_in_executor(
            None, lambda z=zone: _fetch_compute_machine_types_sync(project_id, z)
        )
        for spec in zone_specs:
            if spec.name not in all_specs:
                all_specs[spec.name] = spec

    logger.info("GCP Compute Engine Machine Types API loaded", machine_type_count=len(all_specs))
    return list(all_specs.values())


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
        raise RuntimeError("GCP unit price instance types not found. Run base GCP ingestion first.")

    # Get FX rate for USD->EUR conversion
    rate_usd_eur = await get_rate_to_eur(db, "USD")

    from app.services.ingestion import ensure_instance_type, append_price

    # Fetch machine types from GCP Compute Engine API (required — no static fallback)
    try:
        machine_type_catalog = await _load_live_machine_types(only_eu=only_eu)
    except RuntimeError as e:
        logger.error(
            "GCP machine types ingestion aborted: Compute Engine API unavailable. "
            "Ensure GOOGLE_CLOUD_PROJECT is set and google-cloud-compute is installed.",
            error=str(e),
        )
        return
    if not machine_type_catalog:
        logger.error("GCP Compute Engine API returned no machine types; aborting.")
        return
    logger.info("Using live GCP machine types from Compute Engine API", count=len(machine_type_catalog))

    # Apply family filter if specified
    if families:
        machine_type_catalog = [m for m in machine_type_catalog if m.family in families]

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

        # Process each machine type in catalog
        for spec in machine_type_catalog:
            # Upsert InstanceType with all available metadata
            machine_it = await ensure_instance_type(
                db,
                provider_id=provider.id,
                name=spec.name,
                vcpu=spec.vcpu,
                ram_gb=spec.ram_gb,
                family=spec.family,
                arch=spec.arch,
                gpu_count=spec.gpu_count,
                gpu_model=spec.gpu_model,
                is_current_gen=spec.is_current_gen,
                metadata_json=json.dumps(
                    {
                        "description": f"GCP {spec.name} machine type",
                        "computed_from_unit_prices": True,
                    }
                ),
            )
            await db.flush()

            # Track created machine types
            created_machine_types.add(machine_it.name)

            # Compute total price: cpu_price * vcpu + ram_price * ram_gb
            # GPU instance types have a separate GPU SKU cost not modeled here (conservative estimate)
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
