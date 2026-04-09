"""Scaleway connector: fetch pricing from public Product Catalog API.

Pricing source: Public Product Catalog API (no auth required).
    https://api.scaleway.com/product-catalog/v2alpha1/public-catalog/products

Instance specs: Static lookup table (Scaleway instance types are well-documented).
The public catalog provides pricing and offer IDs but not vCPU/RAM specs.
"""

from __future__ import annotations

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

SCALEWAY_CATALOG_URL = "https://api.scaleway.com/product-catalog/v2alpha1/public-catalog/products"

# Scaleway availability zones
SCALEWAY_ZONES = [
    "fr-par-1", "fr-par-2", "fr-par-3",
    "nl-ams-1", "nl-ams-2", "nl-ams-3",
    "pl-waw-1", "pl-waw-2", "pl-waw-3",
]

ZONE_MAP = {
    "fr-par-1": {"slug": "fr-par-1", "name": "Paris 1", "country": "FR", "is_eu": True},
    "fr-par-2": {"slug": "fr-par-2", "name": "Paris 2", "country": "FR", "is_eu": True},
    "fr-par-3": {"slug": "fr-par-3", "name": "Paris 3", "country": "FR", "is_eu": True},
    "nl-ams-1": {"slug": "nl-ams-1", "name": "Amsterdam 1", "country": "NL", "is_eu": True},
    "nl-ams-2": {"slug": "nl-ams-2", "name": "Amsterdam 2", "country": "NL", "is_eu": True},
    "nl-ams-3": {"slug": "nl-ams-3", "name": "Amsterdam 3", "country": "NL", "is_eu": True},
    "pl-waw-1": {"slug": "pl-waw-1", "name": "Warsaw 1", "country": "PL", "is_eu": True},
    "pl-waw-2": {"slug": "pl-waw-2", "name": "Warsaw 2", "country": "PL", "is_eu": True},
    "pl-waw-3": {"slug": "pl-waw-3", "name": "Warsaw 3", "country": "PL", "is_eu": True},
}

# ---------------------------------------------------------------------------
# Static spec lookup table for Scaleway instance types.
# Sources: https://www.scaleway.com/en/pricing/ and https://www.scaleway.com/en/docs/compute/instances/reference-content/instances/
# (name, vcpu, ram_gb, family, arch, cpu_type, gpu_count, gpu_model, gpu_memory_gb)
# ---------------------------------------------------------------------------

_INSTANCE_SPECS: dict[str, tuple[int, float, str, str, str | None, int | None, str | None, float | None]] = {
    # Development / Shared — DEV1
    "DEV1-S":    (2,  2.0,  "general-purpose", "x86_64", "shared", None, None, None),
    "DEV1-M":    (3,  4.0,  "general-purpose", "x86_64", "shared", None, None, None),
    "DEV1-L":    (4,  8.0,  "general-purpose", "x86_64", "shared", None, None, None),
    "DEV1-XL":   (4,  12.0, "general-purpose", "x86_64", "shared", None, None, None),
    # PLAY2 — ARM (shared)
    "PLAY2-PICO":  (1,  1.0,  "general-purpose", "arm64", "shared", None, None, None),
    "PLAY2-NANO":  (1,  2.0,  "general-purpose", "arm64", "shared", None, None, None),
    "PLAY2-MICRO": (2,  4.0,  "general-purpose", "arm64", "shared", None, None, None),
    # STARDUST1 — Budget
    "STARDUST1-S": (1,  1.0,  "general-purpose", "x86_64", "shared", None, None, None),
    # General-Purpose — GP1
    "GP1-XS":    (4,  16.0, "general-purpose", "x86_64", "dedicated", None, None, None),
    "GP1-S":     (8,  32.0, "general-purpose", "x86_64", "dedicated", None, None, None),
    "GP1-M":     (16, 64.0, "general-purpose", "x86_64", "dedicated", None, None, None),
    "GP1-L":     (32, 128.0,"general-purpose", "x86_64", "dedicated", None, None, None),
    "GP1-XL":    (48, 256.0,"general-purpose", "x86_64", "dedicated", None, None, None),
    # Performance — PRO2
    "PRO2-XXS":  (2,  8.0,  "compute-optimized", "x86_64", "dedicated", None, None, None),
    "PRO2-XS":   (4,  16.0, "compute-optimized", "x86_64", "dedicated", None, None, None),
    "PRO2-S":    (8,  32.0, "compute-optimized", "x86_64", "dedicated", None, None, None),
    "PRO2-M":    (16, 64.0, "compute-optimized", "x86_64", "dedicated", None, None, None),
    "PRO2-L":    (32, 128.0,"compute-optimized", "x86_64", "dedicated", None, None, None),
    # Enterprise — ENT1
    "ENT1-XXS":  (2,  8.0,  "memory-optimized", "x86_64", "dedicated", None, None, None),
    "ENT1-XS":   (4,  16.0, "memory-optimized", "x86_64", "dedicated", None, None, None),
    "ENT1-S":    (8,  32.0, "memory-optimized", "x86_64", "dedicated", None, None, None),
    "ENT1-M":    (16, 64.0, "memory-optimized", "x86_64", "dedicated", None, None, None),
    "ENT1-L":    (32, 128.0,"memory-optimized", "x86_64", "dedicated", None, None, None),
    "ENT1-XL":   (64, 256.0,"memory-optimized", "x86_64", "dedicated", None, None, None),
    "ENT1-2XL":  (96, 384.0,"memory-optimized", "x86_64", "dedicated", None, None, None),
    # POP2 — Latest gen
    "POP2-2C-8G":   (2,  8.0,  "general-purpose", "x86_64", "dedicated", None, None, None),
    "POP2-4C-16G":  (4,  16.0, "general-purpose", "x86_64", "dedicated", None, None, None),
    "POP2-8C-32G":  (8,  32.0, "general-purpose", "x86_64", "dedicated", None, None, None),
    "POP2-16C-64G": (16, 64.0, "general-purpose", "x86_64", "dedicated", None, None, None),
    "POP2-32C-128G":(32, 128.0,"general-purpose", "x86_64", "dedicated", None, None, None),
    "POP2-64C-256G":(64, 256.0,"general-purpose", "x86_64", "dedicated", None, None, None),
    # POP2-HM — High memory
    "POP2-HM-2C-16G":  (2,  16.0, "memory-optimized", "x86_64", "dedicated", None, None, None),
    "POP2-HM-4C-32G":  (4,  32.0, "memory-optimized", "x86_64", "dedicated", None, None, None),
    "POP2-HM-8C-64G":  (8,  64.0, "memory-optimized", "x86_64", "dedicated", None, None, None),
    "POP2-HM-16C-128G":(16, 128.0,"memory-optimized", "x86_64", "dedicated", None, None, None),
    "POP2-HM-32C-256G":(32, 256.0,"memory-optimized", "x86_64", "dedicated", None, None, None),
    "POP2-HM-64C-512G":(64, 512.0,"memory-optimized", "x86_64", "dedicated", None, None, None),
    # POP2-HC — High CPU
    "POP2-HC-2C-4G":  (2,  4.0,  "compute-optimized", "x86_64", "dedicated", None, None, None),
    "POP2-HC-4C-8G":  (4,  8.0,  "compute-optimized", "x86_64", "dedicated", None, None, None),
    "POP2-HC-8C-16G": (8,  16.0, "compute-optimized", "x86_64", "dedicated", None, None, None),
    "POP2-HC-16C-32G":(16, 32.0, "compute-optimized", "x86_64", "dedicated", None, None, None),
    "POP2-HC-32C-64G":(32, 64.0, "compute-optimized", "x86_64", "dedicated", None, None, None),
    "POP2-HC-64C-128G":(64,128.0,"compute-optimized", "x86_64", "dedicated", None, None, None),
    # GPU — H100, L40S
    "H100-1-80G":  (20, 160.0, "gpu", "x86_64", "dedicated", 1, "nvidia-h100", 80.0),
    "H100-2-160G": (40, 320.0, "gpu", "x86_64", "dedicated", 2, "nvidia-h100", 160.0),
    "L40S-1-48G":  (12, 96.0,  "gpu", "x86_64", "dedicated", 1, "nvidia-l40s", 48.0),
    "L40S-2-96G":  (24, 192.0, "gpu", "x86_64", "dedicated", 2, "nvidia-l40s", 96.0),
    # AMP2 — ARM dedicated
    "AMP2-C1":  (1,  4.0,  "general-purpose", "arm64", "dedicated", None, None, None),
    "AMP2-C2":  (2,  8.0,  "general-purpose", "arm64", "dedicated", None, None, None),
    "AMP2-C4":  (4,  16.0, "general-purpose", "arm64", "dedicated", None, None, None),
    "AMP2-C8":  (8,  32.0, "general-purpose", "arm64", "dedicated", None, None, None),
    "AMP2-C12": (12, 48.0, "general-purpose", "arm64", "dedicated", None, None, None),
    "AMP2-C24": (24, 96.0, "general-purpose", "arm64", "dedicated", None, None, None),
    "AMP2-C48": (48, 192.0,"general-purpose", "arm64", "dedicated", None, None, None),
    "AMP2-C60": (60, 240.0,"general-purpose", "arm64", "dedicated", None, None, None),
}


def _infer_family(commercial_type: str) -> str:
    """Infer instance family from commercial type name using common taxonomy."""
    type_lower = commercial_type.lower()
    if "gpu" in type_lower or type_lower.startswith("h100") or type_lower.startswith("l40") or type_lower.startswith("a100"):
        return "gpu"
    elif type_lower.startswith("dev") or type_lower.startswith("stardust"):
        return "general-purpose"
    elif type_lower.startswith("gp") or type_lower.startswith("pop2") and "-hm-" not in type_lower and "-hc-" not in type_lower:
        return "general-purpose"
    elif type_lower.startswith("pro") or "-hc-" in type_lower:
        return "compute-optimized"
    elif type_lower.startswith("ent") or "-hm-" in type_lower:
        return "memory-optimized"
    elif type_lower.startswith("play") or type_lower.startswith("amp"):
        return "general-purpose"
    else:
        return "general-purpose"


# ---------------------------------------------------------------------------
# Public catalog fetcher
# ---------------------------------------------------------------------------


async def _fetch_catalog_instance_prices() -> dict[str, dict[str, float]]:
    """Fetch instance prices from public catalog.

    Returns: {offer_id: {zone: eur_per_hour}} mapping.
    The catalog SKU pattern is /compute/{offer_lower}/run_{zone_underscore}
    """
    prices: dict[str, dict[str, float]] = {}  # offer_id -> {zone -> eur/hr}
    page = 1
    per_page = 100

    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            r = await client.get(
                SCALEWAY_CATALOG_URL,
                params={"product_type": "instance", "page": page, "per_page": per_page},
            )
            r.raise_for_status()
            data = r.json()
            products = data.get("products") or []
            if not products:
                break

            for p in products:
                sku = p.get("sku", "")
                if not sku.startswith("/compute/") or "/run" not in sku:
                    continue

                # Extract offer_id from properties or product name
                props = p.get("properties", {}).get("instance", {})
                offer_id = props.get("offer_id", "")
                if not offer_id:
                    # Derive from product name: "DEV1-S Instance" -> "DEV1-S"
                    product_name = p.get("product", "")
                    if " Instance" in product_name:
                        offer_id = product_name.replace(" Instance", "").strip()
                    else:
                        continue

                # Extract zone from locality or SKU
                locality = p.get("locality", {})
                zone = locality.get("zone", "")
                if not zone:
                    # Try to extract from SKU: /compute/dev1_s/run_frpar1 -> fr-par-1
                    parts = sku.split("/")
                    for part in parts:
                        if part.startswith("run_"):
                            raw_zone = part[4:]
                            # Convert frpar1 -> fr-par-1
                            zone = _normalize_zone(raw_zone)
                            break

                if not zone:
                    # Global pricing — apply to all zones
                    zone = "__global__"

                # Extract price
                price_info = p.get("price", {}).get("retail_price", {})
                units = int(price_info.get("units", 0))
                nanos = int(price_info.get("nanos", 0))
                eur_per_hour = units + nanos / 1e9

                if eur_per_hour <= 0:
                    continue

                if offer_id not in prices:
                    prices[offer_id] = {}
                prices[offer_id][zone] = eur_per_hour

            if len(products) < per_page:
                break
            page += 1

    return prices


def _normalize_zone(raw: str) -> str:
    """Convert catalog zone codes (e.g. frpar1, nlams1) to standard format (fr-par-1)."""
    raw = raw.lower().replace("_", "")
    zone_map = {
        "frpar1": "fr-par-1", "frpar2": "fr-par-2", "frpar3": "fr-par-3",
        "nlams1": "nl-ams-1", "nlams2": "nl-ams-2", "nlams3": "nl-ams-3",
        "plwaw1": "pl-waw-1", "plwaw2": "pl-waw-2", "plwaw3": "pl-waw-3",
    }
    return zone_map.get(raw, raw)


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


async def ingest(
    db: AsyncSession,
    only_eu: bool = False,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest Scaleway instance types and pricing.

    Uses public Product Catalog API for pricing (no auth) and static spec table
    for instance specifications.
    """
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning("Scaleway only supports on-demand pricing", purchase_model=purchase_model)
        purchase = PurchaseModel.ON_DEMAND

    provider = await ensure_provider(
        db,
        slug="scaleway",
        name="Scaleway",
        website="https://www.scaleway.com",
        type=ProviderType.EU,
    )
    await db.flush()

    # Create regions
    region_by_zone = {}
    for zone in SCALEWAY_ZONES:
        zone_info = ZONE_MAP.get(zone)
        if not zone_info:
            continue
        is_eu = zone_info["is_eu"]
        if only_eu and not is_eu:
            continue
        region = await ensure_region(
            db,
            provider_id=provider.id,
            slug=zone_info["slug"],
            name=zone_info["name"],
            country=zone_info["country"],
            is_eu=is_eu,
        )
        region_by_zone[zone] = region
    await db.flush()

    await get_rate_to_eur(db, "EUR")

    # Fetch pricing from public catalog
    try:
        catalog_prices = await _fetch_catalog_instance_prices()
        logger.info("Fetched Scaleway catalog prices", offer_count=len(catalog_prices))
    except Exception as e:
        logger.error("Failed to fetch Scaleway public catalog", error=str(e))
        raise

    inserted_prices = 0
    instance_types_seen: set[str] = set()

    for offer_id, zone_prices in catalog_prices.items():
        # Normalize offer_id to match spec table (uppercase)
        offer_upper = offer_id.upper()
        spec = _INSTANCE_SPECS.get(offer_upper)
        if not spec:
            # Try without case sensitivity
            for k, v in _INSTANCE_SPECS.items():
                if k.upper() == offer_upper:
                    spec = v
                    offer_upper = k
                    break

        if not spec:
            continue

        vcpu, ram_gb, family, arch, cpu_type, gpu_count, gpu_model, gpu_memory_gb = spec

        it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=offer_upper,
            vcpu=vcpu,
            ram_gb=ram_gb,
            family=family,
            arch=arch,
            cpu_type=cpu_type,
            is_current_gen=True,
            gpu_count=gpu_count,
            gpu_model=gpu_model,
            gpu_memory_gb=gpu_memory_gb,
        )
        await db.flush()
        instance_types_seen.add(offer_upper)

        # Insert prices per zone
        for zone, eur_per_hour in zone_prices.items():
            if zone == "__global__":
                # Global price applies to all zones
                for z, region in region_by_zone.items():
                    p = await append_price(
                        db,
                        instance_type_id=it.id,
                        region_id=region.id,
                        purchase_model=purchase,
                        currency="EUR",
                        price_per_hour=eur_per_hour,
                        price_eur_per_hour=eur_per_hour,
                    )
                    if p:
                        inserted_prices += 1
            else:
                region = region_by_zone.get(zone)
                if not region:
                    continue
                p = await append_price(
                    db,
                    instance_type_id=it.id,
                    region_id=region.id,
                    purchase_model=purchase,
                    currency="EUR",
                    price_per_hour=eur_per_hour,
                    price_eur_per_hour=eur_per_hour,
                )
                if p:
                    inserted_prices += 1

    logger.info(
        "Scaleway ingestion completed",
        provider_slug="scaleway",
        regions=len(region_by_zone),
        instance_types=len(instance_types_seen),
        price_rows_inserted=inserted_prices,
    )
