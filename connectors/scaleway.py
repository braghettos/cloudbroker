"""Scaleway connector: fetch instance types and pricing, ingest into DB."""

from __future__ import annotations

import os
from decimal import Decimal

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

SCALEWAY_BASE = "https://api.scaleway.com/instance/v1"

# Scaleway availability zones (regions)
# EU zones: fr-par-1/2/3 (Paris), nl-ams-1/2/3 (Amsterdam), pl-waw-1/2/3 (Warsaw)
SCALEWAY_ZONES = [
    "fr-par-1",
    "fr-par-2",
    "fr-par-3",  # Paris, France
    "nl-ams-1",
    "nl-ams-2",
    "nl-ams-3",  # Amsterdam, Netherlands
    "pl-waw-1",
    "pl-waw-2",
    "pl-waw-3",  # Warsaw, Poland
]

# Map zone codes to region metadata
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


async def _fetch_instance_types(token: str, zone: str) -> list[dict]:
    """Fetch instance types (products) from Scaleway API for a specific zone."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{SCALEWAY_BASE}/zones/{zone}/products/servers",
            headers={
                "X-Auth-Token": token,
                "Content-Type": "application/json",
            },
        )
        r.raise_for_status()
        data = r.json()
        # Response can be {"servers": [...]} or {"servers": {...}} where keys are instance type names
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            servers = data.get("servers")
            if isinstance(servers, list):
                return servers
            # If servers is a dict where keys are instance type names
            if isinstance(servers, dict):
                # Convert to list of dicts, adding the key as "name" field
                result = []
                for name, specs in servers.items():
                    if isinstance(specs, dict):
                        specs_with_name = specs.copy()
                        specs_with_name["_instance_type_name"] = name
                        result.append(specs_with_name)
                return result
        return []


async def _check_availability(token: str, zone: str, commercial_type: str) -> bool:
    """Check if an instance type is available in a zone."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{SCALEWAY_BASE}/zones/{zone}/products/servers/availability",
                headers={
                    "X-Auth-Token": token,
                    "Content-Type": "application/json",
                },
                params={"commercial_type": commercial_type},
            )
            r.raise_for_status()
            # Availability endpoint returns availability info
            return True  # If endpoint succeeds, assume available
    except Exception:
        return False


def _price_per_hour_eur(price_hourly: float | None) -> float | None:
    """Extract hourly price in EUR from Scaleway price object."""
    try:
        if price_hourly is not None:
            return float(Decimal(str(price_hourly)))
    except (TypeError, ValueError):
        pass
    return None


def _infer_family(commercial_type: str) -> str:
    """Infer instance family from commercial type name."""
    type_lower = commercial_type.lower()
    # GPU check must come before "gp" (general-purpose) since "gpu" starts with "gp"
    if "gpu" in type_lower or type_lower.startswith("h100") or type_lower.startswith("l40"):
        return "gpu"
    elif type_lower.startswith("dev") or type_lower.startswith("stardust"):
        return "development"
    elif type_lower.startswith("gp"):
        return "general-purpose"
    elif type_lower.startswith("pro"):
        return "performance"
    elif type_lower.startswith("baremetal"):
        return "bare-metal"
    elif type_lower.startswith("render"):
        return "render"
    else:
        return "standard"


async def ingest(
    db: AsyncSession,
    only_eu: bool = False,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest Scaleway instance types and pricing into database."""
    access_key = os.environ.get("SCALEWAY_ACCESS_KEY") or os.environ.get("SCW_ACCESS_KEY")
    secret_key = os.environ.get("SCALEWAY_SECRET_KEY") or os.environ.get("SCW_SECRET_KEY")

    if not access_key or not secret_key:
        raise RuntimeError(
            "SCALEWAY_ACCESS_KEY/SCALEWAY_SECRET_KEY or SCW_ACCESS_KEY/SCW_SECRET_KEY "
            "environment variables are required for Scaleway ingestion"
        )

    # Scaleway uses secret key as token in X-Auth-Token header
    token = secret_key

    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning(
            "Scaleway only supports on-demand pricing, ignoring purchase_model", purchase_model=purchase_model
        )
        purchase = PurchaseModel.ON_DEMAND

    # Ensure provider
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
    logger.info("Created/updated regions", count=len(region_by_zone))

    # Ensure FX rate for EUR exists (Scaleway prices are in EUR)
    await get_rate_to_eur(db, "EUR")

    # Process instance types per zone
    inserted_prices = 0
    instance_types_seen = set()

    for zone in region_by_zone.keys():
        try:
            instance_types = await _fetch_instance_types(token, zone)
            logger.info("Fetched instance types for zone", zone=zone, count=len(instance_types))

            if instance_types and len(instance_types) > 0 and isinstance(instance_types, list):
                # Log first item structure for debugging
                first_item = instance_types[0]
                if isinstance(first_item, dict):
                    ram_raw = first_item.get("ram")
                    logger.info(
                        "First instance type sample",
                        zone=zone,
                        ram_raw=ram_raw,
                        ram_type=type(ram_raw).__name__ if ram_raw is not None else "None",
                    )

            processed_count = 0
            skipped_no_alt_names = 0
            skipped_no_price = 0

            for it_type in instance_types:
                # Handle case where it_type might not be a dict
                if not isinstance(it_type, dict):
                    logger.warning("Skipping non-dict instance type", zone=zone, type=type(it_type).__name__)
                    continue

                # Scaleway instance type name: use _instance_type_name (from dict key) or alt_names[0] or construct from specs
                commercial_type = it_type.get("_instance_type_name")
                if not commercial_type:
                    alt_names = it_type.get("alt_names") or []
                    if alt_names and isinstance(alt_names, list) and len(alt_names) > 0:
                        commercial_type = str(alt_names[0]).strip()
                    else:
                        # Construct name from specs as fallback: "GP1-XS" style
                        ncpus = it_type.get("ncpus", 0)
                        ram_gb = it_type.get("ram", 0)
                        if ram_gb > 1000:  # Likely MB
                            ram_gb = ram_gb / 1024.0
                        commercial_type = f"GP1-{ncpus}vCPU-{int(ram_gb)}GB"

                if not commercial_type:
                    skipped_no_alt_names += 1
                    continue

                # Extract instance specs
                # Try multiple possible field names
                vcpu = int(it_type.get("ncpus", 0) or it_type.get("cpu", 0) or it_type.get("vcores", 0) or 0)
                ram_value = it_type.get("ram") or it_type.get("memory") or it_type.get("ram_mb") or 0
                # Scaleway API returns RAM in bytes
                if isinstance(ram_value, (int, float)):
                    # Convert bytes to GB: divide by 1024^3
                    ram_gb = float(ram_value) / (1024.0 * 1024.0 * 1024.0)
                else:
                    ram_gb = 0.0

                # Scaleway instances are x86_64 (or ARM for some types, but default to x86_64)
                arch = "x86_64"

                # Infer family from commercial type
                family = _infer_family(commercial_type)

                # GPU: Scaleway GPU instances (H100, L40, etc.) have gpu != 0 in the API
                gpu_count: int | None = None
                gpu_model: str | None = None
                type_lower = commercial_type.lower()
                if "h100" in type_lower:
                    gpu_count = 1
                    gpu_model = "nvidia-h100"
                elif "l40" in type_lower:
                    gpu_count = 1
                    gpu_model = "nvidia-l40s"
                elif "a100" in type_lower:
                    gpu_count = 1
                    gpu_model = "nvidia-a100"
                elif "gpu" in type_lower or family == "gpu":
                    gpu_count = 1

                # Ensure instance type
                it = await ensure_instance_type(
                    db,
                    provider_id=provider.id,
                    name=commercial_type,
                    vcpu=vcpu,
                    ram_gb=ram_gb,
                    family=family,
                    arch=arch,
                    gpu_count=gpu_count,
                    gpu_model=gpu_model,
                )
                await db.flush()
                instance_types_seen.add(commercial_type)

                # Extract pricing (Scaleway prices are in EUR)
                # Scaleway API uses "hourly_price" field
                price_info = it_type.get("hourly_price") or it_type.get("price_per_hour")

                # Handle different price structures
                if price_info is None:
                    price_hourly_eur = None
                elif isinstance(price_info, dict):
                    price_hourly_eur = (
                        price_info.get("price_incl_tax") or price_info.get("price") or price_info.get("value")
                    )
                elif isinstance(price_info, (int, float)):
                    price_hourly_eur = float(price_info)
                else:
                    # Try to convert string or other types
                    try:
                        price_hourly_eur = float(price_info)
                    except (ValueError, TypeError):
                        price_hourly_eur = None

                if price_hourly_eur is not None:
                    price_hourly_eur = _price_per_hour_eur(price_hourly_eur)

                if price_hourly_eur is None or price_hourly_eur <= 0:
                    skipped_no_price += 1
                    continue

                processed_count += 1

                # Check availability (optional, but helps filter unavailable types)
                # For now, we'll ingest all types and let availability be checked at query time
                region = region_by_zone[zone]

                p = await append_price(
                    db,
                    instance_type_id=it.id,
                    region_id=region.id,
                    purchase_model=purchase,
                    currency="EUR",
                    price_per_hour=price_hourly_eur,
                    price_eur_per_hour=price_hourly_eur,
                )
                if p:
                    inserted_prices += 1

        except Exception as e:
            error_msg = str(e)
            if isinstance(e, KeyError):
                error_msg = f"KeyError: missing key '{e.args[0] if e.args else 'unknown'}'"
            logger.warning(
                "Error processing instance types for zone", zone=zone, error=error_msg, error_type=type(e).__name__
            )
            import traceback

            logger.debug("Traceback", traceback=traceback.format_exc())
            continue
        else:
            # Log processing stats if no exception
            if processed_count > 0 or skipped_no_alt_names > 0 or skipped_no_price > 0:
                logger.info(
                    "Zone processing stats",
                    zone=zone,
                    processed=processed_count,
                    skipped_no_alt_names=skipped_no_alt_names,
                    skipped_no_price=skipped_no_price,
                )

    logger.info(
        "Scaleway ingestion completed",
        provider_slug="scaleway",
        regions=len(region_by_zone),
        instance_types=len(instance_types_seen),
        price_rows_inserted=inserted_prices,
    )
