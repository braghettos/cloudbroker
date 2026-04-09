"""OVH connector: fetch flavors and pricing from public catalog (no auth required).

Data source:
    **Public Catalog** (no auth):
    ``GET /order/catalog/public/cloud?ovhSubsidiary=FR`` → ex-VAT hourly prices + specs.

Previously required authenticated Project Flavor API for specs; now derives everything
from the public catalog to eliminate credential requirements.
"""

from __future__ import annotations

import re

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur
from app.core.logging import get_logger
from app.models.price import PurchaseModel
from app.models.provider import ProviderType
from app.services.ingestion import (
    ensure_instance_type,
    ensure_provider,
    ensure_region,
    append_price,
)

logger = get_logger(__name__)

OVH_BASE = "https://api.ovh.com/1.0"

# ---------------------------------------------------------------------------
# Region metadata
# ---------------------------------------------------------------------------

# Known OVH datacenter prefixes → (city, country, is_eu).
_DC_META: dict[str, tuple[str, str, bool]] = {
    "GRA": ("Gravelines", "FR", True),
    "SBG": ("Strasbourg", "FR", True),
    "RBX": ("Roubaix", "FR", True),
    "LIM": ("Limburg", "DE", True),
    "DE": ("Limburg", "DE", True),
    "WAW": ("Warsaw", "PL", True),
    "UK": ("London", "GB", True),
    "LON": ("London", "GB", True),
    "BHS": ("Beauharnois", "CA", False),
    "SGP": ("Singapore", "SG", False),
    "SYD": ("Sydney", "AU", False),
    "EU": ("EU", None, True),
    "CA": ("Canada", "CA", False),
}


def _region_meta(slug: str) -> tuple[str, str | None, bool]:
    """Derive (name, country, is_eu) from an OVH region slug like ``GRA7``."""
    alpha = "".join(c for c in slug if c.isalpha()).upper()
    for length in range(len(alpha), 0, -1):
        candidate = alpha[:length]
        if candidate in _DC_META:
            city, country, is_eu = _DC_META[candidate]
            return f"{city} ({slug})", country, is_eu
    return slug, None, False


# ---------------------------------------------------------------------------
# Spec parsing from plan codes and catalog metadata
# ---------------------------------------------------------------------------

# OVH flavor name pattern: b2-7, c2-30, r2-120, d2-4, t1-45, g-1-1, etc.
# The number after the first dash often encodes RAM in GB for standard flavors
_OVH_SPEC_RE = re.compile(r"^([a-z]+\d*)-(\d+)(?:-(\d+))?$", re.IGNORECASE)

# Known vCPU/RAM mappings for OVH flavor prefixes (GB RAM -> vCPU ratio)
_OVH_VCPU_RATIOS: dict[str, float] = {
    "b2": 4.0,  # general-purpose: 1 vCPU per 4 GB RAM
    "b3": 4.0,
    "c2": 2.0,  # compute-optimized: 1 vCPU per 2 GB RAM
    "c3": 2.0,
    "r2": 8.0,  # memory-optimized: 1 vCPU per 8 GB RAM
    "r3": 8.0,
    "d2": 4.0,  # general-purpose
    "t1": 2.0,  # general-purpose small
    "t2": 2.0,
    "s1": 4.0,  # sandbox
    "i1": 4.0,  # io-optimized
}


def _parse_flavor_specs(flavor_name: str) -> tuple[int, float]:
    """Parse vCPU and RAM from OVH flavor name. Returns (vcpu, ram_gb)."""
    match = _OVH_SPEC_RE.match(flavor_name)
    if not match:
        return 0, 0.0

    prefix = match.group(1).lower()
    number = int(match.group(2))

    # GPU flavors: g-1-1 means 1 GPU with 1 vCPU
    if prefix == "g":
        vcpu = int(match.group(3) or 1)
        # GPU instances typically have high RAM
        ram_gb = float(vcpu * 8)
        return vcpu, ram_gb

    # Standard flavors: number is typically RAM in GB
    ram_gb = float(number)
    ratio = _OVH_VCPU_RATIOS.get(prefix, 4.0)
    vcpu = max(1, round(ram_gb / ratio))

    return vcpu, ram_gb


def _infer_family(flavor_name: str) -> str:
    """Infer instance family from OVH flavor name prefix using common taxonomy."""
    prefix = flavor_name.split("-")[0].lower() if "-" in flavor_name else flavor_name.lower()
    families = {
        "b2": "general-purpose",
        "b3": "general-purpose",
        "c2": "compute-optimized",
        "c3": "compute-optimized",
        "r2": "memory-optimized",
        "r3": "memory-optimized",
        "d2": "general-purpose",
        "i1": "storage-optimized",
        "t1": "general-purpose",
        "t2": "general-purpose",
        "s1": "general-purpose",
        "g": "gpu",
    }
    return families.get(prefix, "general-purpose")


# ---------------------------------------------------------------------------
# Public catalog fetcher
# ---------------------------------------------------------------------------


async def _fetch_public_catalog() -> dict:
    """Fetch the full OVH public cloud catalog (no auth required).

    Returns the raw catalog dict with addons, plans, regions, etc.
    """
    url = f"{OVH_BASE}/order/catalog/public/cloud?ovhSubsidiary=FR"
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


def _extract_prices_from_catalog(catalog: dict) -> dict[str, float]:
    """Extract plan_name -> price_eur_per_hour from catalog addons."""
    price_map: dict[str, float] = {}
    for addon in catalog.get("addons", []):
        plan_code = addon.get("planCode", "")
        if ".consumption" not in plan_code:
            continue
        base_name = plan_code.replace(".consumption", "")
        if ".option." in base_name:
            continue
        for pricing in addon.get("pricings", []):
            raw_price = pricing.get("price")
            raw_tax = pricing.get("tax", 0)
            if raw_price is not None and raw_price > 0:
                ex_vat = (raw_price - raw_tax) / 100_000_000
                price_map[base_name] = round(ex_vat, 6)
                break
    return price_map


def _extract_regions_from_catalog(catalog: dict) -> set[str]:
    """Extract available region slugs from the public catalog plans."""
    regions: set[str] = set()
    for plan in catalog.get("plans", []):
        for config in plan.get("configurations", []):
            if config.get("name") == "region":
                for value in config.get("values", []):
                    if isinstance(value, str) and value.strip():
                        regions.add(value.strip())
    # Also look in addons
    for addon in catalog.get("addons", []):
        for config in addon.get("configurations", []):
            if config.get("name") == "region":
                for value in config.get("values", []):
                    if isinstance(value, str) and value.strip():
                        regions.add(value.strip())
    return regions


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


async def ingest(
    db: AsyncSession,
    only_eu: bool = False,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest OVH instance types and pricing from public catalog (no auth required)."""

    # --- 1. Ensure provider ---
    provider = await ensure_provider(
        db,
        slug="ovh",
        name="OVHcloud",
        website="https://www.ovhcloud.com",
        type=ProviderType.REGIONAL,
    )
    await db.flush()

    # --- 2. Fetch public catalog (no auth) ---
    try:
        catalog = await _fetch_public_catalog()
        logger.info("Fetched OVH public catalog")
    except Exception as e:
        logger.error("Failed to fetch OVH public catalog", error=str(e))
        raise

    # --- 3. Extract prices ---
    catalog_prices = _extract_prices_from_catalog(catalog)
    logger.info("Extracted catalog prices", count=len(catalog_prices))

    # --- 4. Discover and create regions ---
    catalog_regions = _extract_regions_from_catalog(catalog)
    if not catalog_regions:
        # Fallback: derive regions from price map flavor names aren't region-specific
        # Use known OVH regions
        catalog_regions = {"GRA7", "GRA11", "SBG5", "DE1", "WAW1", "UK1", "BHS5"}

    region_by_slug = {}
    for slug in sorted(catalog_regions):
        name, country, is_eu = _region_meta(slug)
        if only_eu and not is_eu:
            continue
        region = await ensure_region(
            db,
            provider_id=provider.id,
            slug=slug,
            name=name,
            country=country,
            is_eu=is_eu,
        )
        region_by_slug[slug] = region
    await db.flush()
    logger.info("Created/updated regions", count=len(region_by_slug))

    # --- 5. FX rate ---
    await get_rate_to_eur(db, "EUR")

    # --- 6. Process flavors from price catalog ---
    inserted_prices = 0
    instance_types_seen: set[str] = set()
    no_specs: set[str] = set()

    purchase = PurchaseModel(purchase_model)

    for flavor_name, price_eur in catalog_prices.items():
        if price_eur <= 0:
            continue

        # Parse specs from flavor name
        vcpu, ram_gb = _parse_flavor_specs(flavor_name)
        if vcpu <= 0 or ram_gb <= 0:
            no_specs.add(flavor_name)
            continue

        family = _infer_family(flavor_name)
        arch = "x86_64"  # OVH primarily x86_64

        # GPU detection
        gpu_count: int | None = None
        gpu_model: str | None = None
        prefix = flavor_name.split("-")[0].lower() if "-" in flavor_name else flavor_name.lower()
        if prefix in ("g", "gpu"):
            parts = flavor_name.split("-")
            if len(parts) >= 2:
                try:
                    gpu_count = int(parts[1])
                except (ValueError, IndexError):
                    gpu_count = 1
            else:
                gpu_count = 1

        # cpu_type: t-series are burstable/shared, b/c/r are dedicated
        cpu_type: str | None = None
        if prefix in ("t1", "t2", "s1"):
            cpu_type = "shared"
        elif prefix in ("b2", "b3", "c2", "c3", "r2", "r3", "d2", "i1", "g"):
            cpu_type = "dedicated"

        it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=flavor_name,
            vcpu=vcpu,
            ram_gb=ram_gb,
            family=family,
            arch=arch,
            gpu_count=gpu_count,
            gpu_model=gpu_model,
            cpu_type=cpu_type,
        )
        await db.flush()
        instance_types_seen.add(flavor_name)

        # Insert price for all regions (OVH uses same price across EU regions)
        for region_slug, region in region_by_slug.items():
            p = await append_price(
                db,
                instance_type_id=it.id,
                region_id=region.id,
                purchase_model=purchase,
                currency="EUR",
                price_per_hour=price_eur,
                price_eur_per_hour=price_eur,
            )
            if p:
                inserted_prices += 1

    if no_specs:
        logger.warning("Flavors without parseable specs", count=len(no_specs), samples=sorted(no_specs)[:10])

    logger.info(
        "OVH ingestion completed",
        instance_types=len(instance_types_seen),
        price_rows_inserted=inserted_prices,
        provider_slug="ovh",
        regions=len(region_by_slug),
    )
