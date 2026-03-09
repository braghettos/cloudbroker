"""GCP Billing Catalog connector (MVP): list Compute Engine SKUs, ingest as pseudo instance types."""

from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur, to_eur
from app.core.logging import get_logger
from app.models.price import PurchaseModel
from app.services.ingestion import (
    append_price,
    ensure_instance_type,
    ensure_region,
    pick_one_per_logical_product,
)

logger = get_logger(__name__)

# MVP: pseudo instance types for CPU and RAM SKUs
GCP_CPU_PSEUDO_NAME = "gcp-compute-cpu"
GCP_RAM_PSEUDO_NAME = "gcp-compute-ram"


def _get_billing_client():
    try:
        from google.cloud import billing_v1
    except ImportError:
        raise RuntimeError(
            "google-cloud-billing is required for GCP ingestion. Install with: pip install google-cloud-billing"
        )
    return billing_v1.CloudCatalogClient()


def _list_services(client) -> list:
    return list(client.list_services())


def _list_skus(client, service_name: str) -> list:
    return list(client.list_skus(parent=service_name))


def _is_compute_linux_vm_sku(sku) -> bool:
    """Filter to Compute Engine Linux VM related SKUs (CPU/RAM)."""
    try:
        cat = sku.category
        if not cat:
            return False
        rf = (cat.resource_family or "").upper()
        rg = (cat.resource_group or "").upper()
        # Compute, VM, OnDemand type
        if "COMPUTE" in rf and "RAM" in rg:
            return True
        if "COMPUTE" in rf and "CPU" in rg:
            return True
        return False
    except Exception:
        return False


def _is_preemptible_sku(sku) -> bool:
    """Check if SKU is for preemptible/spot instances."""
    try:
        cat = sku.category
        if not cat:
            return False
        ut = (cat.usage_type or "").upper()
        # Check usage_type for preemptible indicators
        if "PREEMPTIBLE" in ut or "PREEMPT" in ut:
            return True
        # Check description/display name
        desc = (sku.description or "").upper()
        if "PREEMPTIBLE" in desc or "PREEMPT" in desc:
            return True
        return False
    except Exception:
        return False


def _extract_unit_price_nanos(sku) -> tuple[str, float] | None:
    """Extract currency and price per unit from first tiered rate. Price in nanos -> units."""
    try:
        pi = sku.pricing_info
        if not pi or len(pi) == 0:
            return None
        tiered = pi[0].pricing_expression.tiered_rates
        if not tiered or len(tiered) == 0:
            return None
        unit = tiered[0].unit_price
        if not unit:
            return None
        currency = (unit.currency_code or "USD").strip().upper()
        nanos = unit.nanos or 0
        units = unit.units or 0
        total_units = int(units) + (int(nanos) / 1e9)
        if total_units <= 0:
            return None
        return (currency, total_units)
    except Exception:
        return None


def _service_regions(sku) -> list[str]:
    """Return list of region identifiers from SKU."""
    try:
        return list(sku.service_regions) if sku.service_regions else []
    except Exception:
        return []


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    purchase = PurchaseModel(purchase_model)
    client = _get_billing_client()

    # Find Compute Engine service
    services = _list_services(client)
    compute_service = None
    for s in services:
        if s.display_name and "Compute Engine" in s.display_name:
            compute_service = s
            break
    if not compute_service:
        raise RuntimeError("Compute Engine service not found in GCP Billing Catalog")

    # List SKUs and filter
    skus = _list_skus(client, compute_service.name)
    rate_usd_eur = await get_rate_to_eur(db, "USD")

    # Ensure provider (same as CLI)
    from app.services.ingestion import ensure_provider
    from app.models.provider import ProviderType

    provider = await ensure_provider(
        db,
        slug="gcp",
        name="Google Cloud",
        website="https://cloud.google.com",
        type=ProviderType.HYPERSCALER,
    )
    await db.flush()

    eu_region_prefixes = ("europe-", "eu-")

    def is_eu_region(region_id: str) -> bool:
        return any(region_id.lower().startswith(p) for p in eu_region_prefixes)

    # Build regions from SKU service_regions (unique)
    seen_regions = set()
    for sku in skus:
        if not _is_compute_linux_vm_sku(sku):
            continue
        for reg in _service_regions(sku):
            if only_eu and not is_eu_region(reg):
                continue
            if reg in seen_regions:
                continue
            seen_regions.add(reg)
            slug = reg.lower().replace("_", "-")
            await ensure_region(
                db,
                provider_id=provider.id,
                slug=slug,
                name=reg,
                country=None,
                is_eu=is_eu_region(reg),
            )
    await db.flush()

    from sqlalchemy import select
    from app.models import Region

    regions_stmt = select(Region).where(Region.provider_id == provider.id)
    regions_result = await db.execute(regions_stmt)
    region_by_slug = {r.slug: r for r in regions_result.scalars().all()}

    # MVP: two pseudo instance types (vcpu/ram_gb must be > 0 per schema)
    cpu_it = await ensure_instance_type(
        db,
        provider_id=provider.id,
        name=GCP_CPU_PSEUDO_NAME,
        vcpu=1,
        ram_gb=0.01,
        family="compute",
        metadata_json=json.dumps({"description": "GCP Compute Engine CPU (per vCPU-hour)", "pseudo": True}),
    )
    ram_it = await ensure_instance_type(
        db,
        provider_id=provider.id,
        name=GCP_RAM_PSEUDO_NAME,
        vcpu=1,
        ram_gb=1.0,
        family="compute",
        metadata_json=json.dumps({"description": "GCP Compute Engine RAM (per GB-hour)", "pseudo": True}),
    )
    await db.flush()

    # Map SKU to pseudo type: CPU vs RAM; collect candidates then deduplicate
    candidates: list[dict] = []
    for sku in skus:
        if not _is_compute_linux_vm_sku(sku):
            continue
        is_preemptible = _is_preemptible_sku(sku)
        if purchase == PurchaseModel.ON_DEMAND and is_preemptible:
            continue
        if purchase == PurchaseModel.SPOT and not is_preemptible:
            continue
        price_info = _extract_unit_price_nanos(sku)
        if not price_info:
            continue
        currency, price_per_unit = price_info
        if price_per_unit <= 0:
            continue
        cat = sku.category
        rg = (cat.resource_group or "").upper() if cat else ""
        if "CPU" in rg:
            it = cpu_it
        elif "RAM" in rg:
            it = ram_it
        else:
            continue
        sku_name = getattr(sku, "name", "") or ""
        for reg in _service_regions(sku):
            if only_eu and not is_eu_region(reg):
                continue
            slug = reg.lower().replace("_", "-")
            if slug not in region_by_slug:
                continue
            candidates.append(
                {
                    "it": it,
                    "region_slug": slug,
                    "price_per_unit": price_per_unit,
                    "currency": currency,
                    "sku_name": sku_name,
                }
            )
    # One price per (pseudo_type, region): prefer by SKU name for consistency
    canonical = pick_one_per_logical_product(
        candidates,
        group_key=lambda c: (c["it"].name, c["region_slug"]),
        sort_key=lambda c: (c["sku_name"], c["price_per_unit"]),
    )
    inserted = 0
    for c in canonical:
        region = region_by_slug[c["region_slug"]]
        price_eur = to_eur(c["price_per_unit"], rate_usd_eur) if c["currency"] != "EUR" else c["price_per_unit"]
        p = await append_price(
            db,
            instance_type_id=c["it"].id,
            region_id=region.id,
            purchase_model=purchase,
            currency=c["currency"],
            price_per_hour=c["price_per_unit"],
            price_eur_per_hour=price_eur,
        )
        if p:
            inserted += 1

    logger.info(
        "GCP ingestion completed",
        regions=len(region_by_slug),
        instance_types=2,
        price_rows_inserted=inserted,
    )
