"""Vultr connector: fetch compute plans and pricing from public API.

Pricing source: Public API (no auth required).
    GET https://api.vultr.com/v2/plans
    GET https://api.vultr.com/v2/regions

Vultr is a global provider with 8 EU regions and individual-friendly pricing.
Plans include hourly and monthly USD pricing with optional regional overrides.
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

VULTR_API_BASE = "https://api.vultr.com/v2"

# Family mapping from Vultr plan type
_FAMILY_MAP: dict[str, str] = {
    "vc2": "general-purpose",
    "vhf": "compute-optimized",
    "vhp": "compute-optimized",
    "voc": "compute-optimized",
    "vcg": "gpu",
    "vbm": "compute-optimized",
}

# CPU type mapping
_CPU_TYPE_MAP: dict[str, str] = {
    "vc2": "shared",
    "vhf": "dedicated",
    "vhp": "dedicated",
    "voc": "dedicated",
    "vcg": "dedicated",
    "vbm": "dedicated",
}

# EU region metadata
_EU_REGIONS: dict[str, tuple[str, str, bool]] = {
    "ams": ("Amsterdam", "NL", True),
    "cdg": ("Paris", "FR", True),
    "fra": ("Frankfurt", "DE", True),
    "lhr": ("London", "GB", True),
    "mad": ("Madrid", "ES", True),
    "man": ("Manchester", "GB", True),
    "sto": ("Stockholm", "SE", True),
    "waw": ("Warsaw", "PL", True),
}

# Non-EU regions
_OTHER_REGIONS: dict[str, tuple[str, str, bool]] = {
    "ewr": ("New Jersey", "US", False),
    "ord": ("Chicago", "US", False),
    "dfw": ("Dallas", "US", False),
    "lax": ("Los Angeles", "US", False),
    "atl": ("Atlanta", "US", False),
    "mia": ("Miami", "US", False),
    "sjc": ("Silicon Valley", "US", False),
    "sea": ("Seattle", "US", False),
    "hnl": ("Honolulu", "US", False),
    "sao": ("Sao Paulo", "BR", False),
    "yto": ("Toronto", "CA", False),
    "nrt": ("Tokyo", "JP", False),
    "itm": ("Osaka", "JP", False),
    "sgp": ("Singapore", "SG", False),
    "icn": ("Seoul", "KR", False),
    "bom": ("Mumbai", "IN", False),
    "del": ("Delhi", "IN", False),
    "blr": ("Bangalore", "IN", False),
    "syd": ("Sydney", "AU", False),
    "mel": ("Melbourne", "AU", False),
    "jnb": ("Johannesburg", "ZA", False),
    "tlv": ("Tel Aviv", "IL", False),
}

_ALL_REGIONS = {**_EU_REGIONS, **_OTHER_REGIONS}


async def _fetch_plans() -> list[dict]:
    """Fetch all compute plans from Vultr public API. No auth required."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{VULTR_API_BASE}/plans")
        r.raise_for_status()
        return r.json().get("plans", [])


async def _fetch_regions() -> list[dict]:
    """Fetch all regions from Vultr public API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{VULTR_API_BASE}/regions")
        r.raise_for_status()
        return r.json().get("regions", [])


async def ingest(
    db: AsyncSession,
    only_eu: bool = False,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest Vultr compute plans and pricing. Public API, no auth required."""
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning("Vultr only supports on-demand pricing", purchase_model=purchase_model)
        purchase = PurchaseModel.ON_DEMAND

    provider = await ensure_provider(
        db,
        slug="vultr",
        name="Vultr",
        website="https://www.vultr.com",
        type=ProviderType.REGIONAL,
    )
    await db.flush()

    # Fetch regions and plans
    api_regions = await _fetch_regions()
    plans = await _fetch_plans()
    logger.info("Fetched Vultr data", regions=len(api_regions), plans=len(plans))

    # Build region map from API data enriched with our metadata
    region_by_id: dict[str, object] = {}
    for reg in api_regions:
        rid = reg.get("id", "")
        if not rid:
            continue
        meta = _ALL_REGIONS.get(rid)
        if meta:
            name, country, is_eu = meta
        else:
            name = reg.get("city", rid)
            country = reg.get("country", "")[:2] if reg.get("country") else None
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

    # FX rate for USD → EUR
    rate_usd_eur = await get_rate_to_eur(db, "USD")

    inserted_prices = 0
    instance_types_seen: set[str] = set()

    for plan in plans:
        plan_id = plan.get("id", "")
        if not plan_id:
            continue

        vcpu = plan.get("vcpu_count", 0)
        ram_mb = plan.get("ram", 0)
        ram_gb = float(ram_mb) / 1024.0 if ram_mb else 0.0
        disk_gb = float(plan.get("disk", 0)) or None
        disk_type = (plan.get("disk_type") or "").lower()

        if vcpu <= 0 or ram_gb <= 0:
            continue

        plan_type = plan.get("type", "vc2")
        family = _FAMILY_MAP.get(plan_type, "general-purpose")
        cpu_type = _CPU_TYPE_MAP.get(plan_type, "shared")

        # GPU detection
        gpu_count = plan.get("gpu_vram_gb", None)
        if gpu_count and gpu_count > 0:
            gpu_count = 1  # Vultr GPU plans have 1 GPU
        else:
            gpu_count = None

        it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=plan_id,
            vcpu=vcpu,
            ram_gb=ram_gb,
            family=family,
            arch="x86_64",
            cpu_type=cpu_type,
            is_current_gen=True,
            disk_gb=disk_gb,
            disk_type="local_ssd" if "ssd" in disk_type else ("local_nvme" if "nvme" in disk_type else None),
            gpu_count=gpu_count,
        )
        await db.flush()
        instance_types_seen.add(plan_id)

        # Base hourly price (USD)
        base_hourly = plan.get("hourly_cost", 0)
        if not base_hourly or base_hourly <= 0:
            continue

        # Regional price overrides
        location_cost = plan.get("location_cost", {}) or {}
        locations = plan.get("locations", []) or []

        for loc_id in locations:
            region = region_by_id.get(loc_id)
            if not region:
                continue

            # Use regional override if exists, else base price
            override = location_cost.get(loc_id, {})
            hourly_usd = override.get("hourly_cost", base_hourly) if override else base_hourly
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
        "Vultr ingestion completed",
        provider_slug="vultr",
        regions=len(region_by_id),
        instance_types=len(instance_types_seen),
        price_rows_inserted=inserted_prices,
    )
