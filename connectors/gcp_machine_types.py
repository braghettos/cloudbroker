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


def _fetch_compute_machine_types_sync(project_id: str, zone: str) -> list[MachineTypeSpec]:
    """Fetch machine types from GCP Compute Engine API for a zone (synchronous).

    Raises RuntimeError if google-cloud-compute is not installed.
    Returns empty list if API call fails for the specific zone.
    """
    try:
        from google.cloud import compute_v1  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "google-cloud-compute is not installed. Install it with: pip install google-cloud-compute"
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
            specs.append(
                MachineTypeSpec(
                    name=name,
                    vcpu=vcpu,
                    ram_gb=ram_gb,
                    family=family,
                    arch=arch,
                    gpu_count=gpu_count,
                    gpu_model=gpu_model,
                    is_current_gen=family not in ("n1", "f1", "g1"),
                )
            )
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
    from app.core.config import settings as app_settings

    project_id = app_settings.google_cloud_project or os.environ.get("GCP_PROJECT_ID") or ""
    if not project_id:
        raise RuntimeError(
            "google_cloud_project is required for GCP Compute Engine API (set via env var or secret file)"
        )

    eu_zones = [
        "europe-west1-b",
        "europe-west2-a",
        "europe-west3-a",
        "europe-west4-a",
        "europe-north1-a",
        "europe-west6-a",
        "europe-west9-a",
        "europe-southwest1-a",
        "europe-central2-a",
    ]
    global_zones = eu_zones + [
        "us-central1-a",
        "us-east1-b",
        "us-east4-a",
        "us-west1-a",
        "us-west2-a",
        "us-west3-a",
        "us-west4-a",
        "northamerica-northeast1-a",
        "southamerica-east1-a",
        "asia-east1-a",
        "asia-east2-a",
        "asia-northeast1-a",
        "asia-northeast3-a",
        "asia-southeast1-a",
        "asia-southeast2-a",
        "asia-south1-a",
        "australia-southeast1-a",
        "me-west1-a",
        "me-central1-a",
        "africa-south1-a",
    ]
    zones_to_check = eu_zones if only_eu else global_zones

    loop = asyncio.get_event_loop()
    all_specs: dict[str, MachineTypeSpec] = {}

    for zone in zones_to_check:
        zone_specs = await loop.run_in_executor(None, lambda z=zone: _fetch_compute_machine_types_sync(project_id, z))
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
