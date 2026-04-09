"""Linode (Akamai Cloud) connector: fetch instance types and pricing from public API.

Pricing source: Public API (no auth required).
    GET https://api.linode.com/v4/linode/types
    GET https://api.linode.com/v4/regions

Linode/Akamai is a global provider with 10 EU regions and individual-friendly pricing.
Types include hourly and monthly USD pricing with optional regional overrides.
"""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur, to_eur
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

LINODE_API_BASE = "https://api.linode.com/v4"

# Family mapping from Linode class
_FAMILY_MAP: dict[str, str] = {
    "nanode": "general-purpose",
    "standard": "general-purpose",
    "dedicated": "compute-optimized",
    "highmem": "memory-optimized",
    "premium": "compute-optimized",
    "gpu": "gpu",
    "accelerated": "gpu",
}

# CPU type mapping
_CPU_TYPE_MAP: dict[str, str] = {
    "nanode": "shared",
    "standard": "shared",
    "dedicated": "dedicated",
    "highmem": "dedicated",
    "premium": "dedicated",
    "gpu": "dedicated",
    "accelerated": "dedicated",
}

# EU region metadata: region_id -> (name, country, is_eu)
_EU_REGIONS: dict[str, tuple[str, str, bool]] = {
    "eu-central": ("Frankfurt", "DE", True),
    "eu-west": ("London", "GB", True),
    "nl-ams": ("Amsterdam", "NL", True),
    "gb-lon": ("London 2", "GB", True),
    "de-fra-2": ("Frankfurt 2", "DE", True),
    "fr-par": ("Paris", "FR", True),
    "fr-par-2": ("Paris 2", "FR", True),
    "it-mil": ("Milan", "IT", True),
    "es-mad": ("Madrid", "ES", True),
    "se-sto": ("Stockholm", "SE", True),
}


async def _fetch_types() -> list[dict]:
    """Fetch all Linode types from public API. No auth required."""
    all_types: list[dict] = []
    page = 1
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            r = await client.get(f"{LINODE_API_BASE}/linode/types", params={"page": page, "page_size": 100})
            r.raise_for_status()
            data = r.json()
            types = data.get("data", [])
            if not types:
                break
            all_types.extend(types)
            if page >= data.get("pages", 1):
                break
            page += 1
    return all_types


async def _fetch_regions() -> list[dict]:
    """Fetch all Linode regions from public API."""
    all_regions: list[dict] = []
    page = 1
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            r = await client.get(f"{LINODE_API_BASE}/regions", params={"page": page, "page_size": 100})
            r.raise_for_status()
            data = r.json()
            regions = data.get("data", [])
            if not regions:
                break
            all_regions.extend(regions)
            if page >= data.get("pages", 1):
                break
            page += 1
    return all_regions


async def ingest(
    db: AsyncSession,
    only_eu: bool = False,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest Linode instance types and pricing. Public API, no auth required."""
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning("Linode only supports on-demand pricing", purchase_model=purchase_model)
        purchase = PurchaseModel.ON_DEMAND

    provider = await ensure_provider(
        db,
        slug="linode",
        name="Linode (Akamai)",
        website="https://www.linode.com",
        type=ProviderType.REGIONAL,
    )
    await db.flush()

    # Fetch regions
    api_regions = await _fetch_regions()
    region_by_id: dict[str, object] = {}

    for reg in api_regions:
        rid = reg.get("id", "")
        if not rid:
            continue

        # Check our EU metadata first, then derive from API
        meta = _EU_REGIONS.get(rid)
        if meta:
            name, country, is_eu = meta
        else:
            name = reg.get("label", rid)
            country_raw = reg.get("country", "")
            country = country_raw.upper()[:2] if country_raw else None
            is_eu = False

        if only_eu and not is_eu:
            continue

        region = await ensure_region(
            db,
            provider_id=provider.id,
            slug=rid,
            name=name,
            country=country,
            is_eu=is_eu,
        )
        region_by_id[rid] = region
    await db.flush()

    # Build region price override lookup: type_id -> {region_id: hourly_usd}
    # (populated from type response data below)

    # Fetch types
    types = await _fetch_types()
    logger.info("Fetched Linode data", regions=len(region_by_id), types=len(types))

    rate_usd_eur = await get_rate_to_eur(db, "USD")

    inserted_prices = 0
    instance_types_seen: set[str] = set()

    for t in types:
        type_id = t.get("id", "")
        if not type_id:
            continue

        vcpus = t.get("vcpus", 0)
        memory_mb = t.get("memory", 0)
        ram_gb = float(memory_mb) / 1024.0 if memory_mb else 0.0
        disk_mb = t.get("disk", 0)
        disk_gb = float(disk_mb) / 1024.0 if disk_mb else None
        gpus = t.get("gpus", 0) or None
        transfer_gb = t.get("transfer", 0)
        transfer_tb = float(transfer_gb) / 1000.0 if transfer_gb else None

        if vcpus <= 0 or ram_gb <= 0:
            continue

        type_class = t.get("class", "standard")
        family = _FAMILY_MAP.get(type_class, "general-purpose")
        cpu_type = _CPU_TYPE_MAP.get(type_class, "shared")

        it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=type_id,
            vcpu=vcpus,
            ram_gb=ram_gb,
            family=family,
            arch="x86_64",
            cpu_type=cpu_type,
            is_current_gen=True,
            disk_gb=disk_gb,
            disk_type="local_ssd",
            gpu_count=gpus,
            transfer_tb=transfer_tb,
        )
        await db.flush()
        instance_types_seen.add(type_id)

        # Base price
        price_info = t.get("price", {}) or {}
        base_hourly = price_info.get("hourly", 0)
        if not base_hourly or base_hourly <= 0:
            continue

        # Build regional price overrides
        region_prices: dict[str, float] = {}
        for rp in t.get("region_prices", []) or []:
            rp_id = rp.get("id", "")
            rp_hourly = rp.get("hourly", 0)
            if rp_id and rp_hourly and rp_hourly > 0:
                region_prices[rp_id] = rp_hourly

        # Insert price for each available region
        for rid, region in region_by_id.items():
            hourly_usd = region_prices.get(rid, base_hourly)
            if hourly_usd <= 0:
                continue

            price_eur = to_eur(hourly_usd, rate_usd_eur)
            p = await append_price(
                db,
                instance_type_id=it.id,
                region_id=region.id,
                purchase_model=purchase,
                currency="USD",
                price_per_hour=hourly_usd,
                price_eur_per_hour=price_eur,
            )
            if p:
                inserted_prices += 1

    logger.info(
        "Linode ingestion completed",
        provider_slug="linode",
        regions=len(region_by_id),
        instance_types=len(instance_types_seen),
        price_rows_inserted=inserted_prices,
    )
