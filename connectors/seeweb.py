"""Seeweb connector: fetch plans and regions, ingest into DB."""

from __future__ import annotations

import os
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur, to_eur
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

SEEWEB_BASE = "https://api.seeweb.it/ecs/v2"

# EU regions based on Seeweb location codes
# Seeweb regions: it-mi2 (Milano, Italy), it-fr2 (Frosinone, Italy),
# ch-lug1 (Lugano, Switzerland - not EU), bg-sof1 (Sofia, Bulgaria)
EU_REGION_CODES = {
    "it-mi2",  # Milano, Italy
    "it-fr2",  # Frosinone, Italy
    "bg-sof1",  # Sofia, Bulgaria
}

# Map region codes to country codes and names
REGION_MAP = {
    "it-mi2": {"country": "IT", "name": "Milano", "is_eu": True},
    "it-fr2": {"country": "IT", "name": "Frosinone", "is_eu": True},
    "ch-lug1": {"country": "CH", "name": "Lugano", "is_eu": False},
    "bg-sof1": {"country": "BG", "name": "Sofia", "is_eu": True},
}


async def _get_jwt_token(username: str, password: str) -> str:
    """Authenticate and get JWT token from Seeweb API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{SEEWEB_BASE}/login",
            json={"username": username, "password": password},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "ok" or not data.get("token"):
            raise RuntimeError(f"Seeweb login failed: {data}")
        return data["token"]


async def _fetch_plans(token: str) -> list[dict]:
    """Fetch all plans from Seeweb API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{SEEWEB_BASE}/plans",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "ok":
            raise RuntimeError(f"Seeweb plans fetch failed: {data}")
        return data.get("plans") or []


def _is_eu_region(location_code: str) -> bool:
    """Determine if a region is EU based on location code."""
    return location_code.lower() in EU_REGION_CODES


def _price_per_hour_usd(plan: dict) -> float | None:
    """Extract hourly price in USD from Seeweb plan object."""
    try:
        # Prefer hourly pricing
        hourly = plan.get("hourly_price")
        if hourly is not None:
            return float(Decimal(str(hourly)))
        # Fallback: monthly -> hourly (730 hours per month)
        monthly = plan.get("montly_price")  # Note: API uses "montly_price" (typo)
        if monthly is not None:
            return float(Decimal(str(monthly)) / 730)
    except (TypeError, ValueError):
        pass
    return None


async def ingest(
    db: AsyncSession,
    only_eu: bool = False,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest Seeweb plans and regions into database."""
    username = os.environ.get("SEEWEB_USERNAME")
    password = os.environ.get("SEEWEB_PASSWORD")

    if not username or not password:
        raise RuntimeError(
            "SEEWEB_USERNAME and SEEWEB_PASSWORD environment variables are required for Seeweb ingestion"
        )

    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning("Seeweb only supports on-demand pricing, ignoring purchase_model", purchase_model=purchase_model)
        purchase = PurchaseModel.ON_DEMAND

    # Authenticate and get JWT token
    token = await _get_jwt_token(username, password)
    logger.info("Authenticated with Seeweb API")

    # Fetch plans
    plans_list = await _fetch_plans(token)
    logger.info("Fetched Seeweb plans", plans_count=len(plans_list))

    # Ensure provider
    provider = await ensure_provider(
        db,
        slug="seeweb",
        name="Seeweb",
        website="https://www.seeweb.it",
        type=ProviderType.EU,
    )
    await db.flush()

    # Collect all unique regions from plans
    all_regions = {}
    for plan in plans_list:
        available_regions = plan.get("available_regions") or []
        for reg_info in available_regions:
            location_code = reg_info.get("location", "").strip().lower()
            if not location_code:
                continue

            # Get region metadata
            region_info = REGION_MAP.get(location_code)
            if not region_info:
                # Unknown region - create default entry
                region_info = {
                    "country": None,
                    "name": reg_info.get("description") or location_code,
                    "is_eu": _is_eu_region(location_code),
                }

            is_eu = region_info["is_eu"]
            if only_eu and not is_eu:
                continue

            if location_code not in all_regions:
                all_regions[location_code] = region_info

    # Create regions
    region_by_code = {}
    for location_code, region_info in all_regions.items():
        region = await ensure_region(
            db,
            provider_id=provider.id,
            slug=location_code,
            name=region_info["name"],
            country=region_info["country"],
            is_eu=region_info["is_eu"],
        )
        region_by_code[location_code] = region

    await db.flush()
    logger.info("Created/updated regions", count=len(region_by_code))

    # Process plans (instance types)
    inserted_prices = 0
    instance_types_seen = set()

    # Get FX rate for USD to EUR conversion
    rate_usd_eur = await get_rate_to_eur(db, "USD")

    for plan in plans_list:
        plan_name = (plan.get("name") or "").strip()
        if not plan_name or not plan.get("available"):
            continue

        # Extract instance type details
        vcpu = int(plan.get("cpu") or 0)
        ram_mb = int(plan.get("ram") or 0)
        ram_gb = float(ram_mb) / 1024.0 if ram_mb else 0.0

        # Seeweb instances are x86_64
        arch = "x86_64"

        # Family can be inferred from host_type or plan name
        host_type = plan.get("host_type", "").upper()
        if host_type == "ECS":
            family = "standard"
        elif "GPU" in host_type or plan.get("gpu", 0) > 0:
            family = "gpu"
        else:
            family = "standard"

        # Ensure instance type
        it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=plan_name,
            vcpu=vcpu,
            ram_gb=ram_gb,
            family=family,
            arch=arch,
        )
        await db.flush()
        instance_types_seen.add(plan_name)

        # Extract pricing
        price_hourly_usd = _price_per_hour_usd(plan)
        if price_hourly_usd is None or price_hourly_usd <= 0:
            logger.warning("Skipping plan with invalid pricing", plan_name=plan_name)
            continue

        # Convert USD to EUR
        price_eur_per_hour = to_eur(price_hourly_usd, rate_usd_eur)

        # Get available regions for this plan
        available_regions = plan.get("available_regions") or []

        # Create price entries for each available region
        for reg_info in available_regions:
            location_code = reg_info.get("location", "").strip().lower()
            if not location_code or location_code not in region_by_code:
                continue  # Skip regions not in our filtered list

            region = region_by_code[location_code]

            p = await append_price(
                db,
                instance_type_id=it.id,
                region_id=region.id,
                purchase_model=purchase,
                currency="USD",
                price_per_hour=price_hourly_usd,
                price_eur_per_hour=price_eur_per_hour,
            )
            if p:
                inserted_prices += 1

    logger.info(
        "Seeweb ingestion completed",
        provider_slug="seeweb",
        regions=len(region_by_code),
        instance_types=len(instance_types_seen),
        price_rows_inserted=inserted_prices,
    )
