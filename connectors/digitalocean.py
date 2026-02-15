"""DigitalOcean connector: fetch sizes and regions, ingest into DB."""

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

DO_BASE = "https://api.digitalocean.com/v2"

# EU regions based on DigitalOcean region slugs
# DigitalOcean EU regions: ams3 (Amsterdam), fra1 (Frankfurt), lon1 (London),
# par1 (Paris), sgp1 (Singapore - not EU), nyc1/nyc2/nyc3 (NYC - not EU), etc.
EU_REGION_SLUGS = {
    "ams3",  # Amsterdam, Netherlands
    "fra1",  # Frankfurt, Germany
    "lon1",  # London, UK
    "par1",  # Paris, France
}

# Map region slugs to country codes for EU regions
REGION_COUNTRY_MAP = {
    "ams3": "NL",
    "fra1": "DE",
    "lon1": "GB",
    "par1": "FR",
}


async def _fetch_regions(token: str) -> list[dict]:
    """Fetch all regions from DigitalOcean API."""
    regions = []
    page = 1
    per_page = 200  # Max per page

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            r = await client.get(
                f"{DO_BASE}/regions",
                headers={"Authorization": f"Bearer {token}"},
                params={"per_page": per_page, "page": page},
            )
            r.raise_for_status()
            data = r.json()
            page_regions = data.get("regions") or []
            if not page_regions:
                break
            regions.extend(page_regions)
            # Check if there are more pages
            if len(page_regions) < per_page:
                break
            page += 1

    return regions


async def _fetch_sizes(token: str) -> list[dict]:
    """Fetch all sizes (Droplet plans) from DigitalOcean API."""
    sizes = []
    page = 1
    per_page = 200  # Max per page

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            r = await client.get(
                f"{DO_BASE}/sizes",
                headers={"Authorization": f"Bearer {token}"},
                params={"per_page": per_page, "page": page},
            )
            r.raise_for_status()
            data = r.json()
            page_sizes = data.get("sizes") or []
            if not page_sizes:
                break
            sizes.extend(page_sizes)
            # Check if there are more pages
            if len(page_sizes) < per_page:
                break
            page += 1

    return sizes


def _price_per_hour_usd(price_monthly: float | None, price_hourly: float | None) -> float | None:
    """Extract hourly price in USD from DigitalOcean price fields."""
    try:
        # Prefer hourly pricing if available
        if price_hourly is not None:
            return float(Decimal(str(price_hourly)))
        # Fallback: monthly -> hourly (730 hours per month)
        if price_monthly is not None:
            return float(Decimal(str(price_monthly)) / 730)
    except (TypeError, ValueError):
        pass
    return None


def _is_eu_region(slug: str) -> bool:
    """Determine if a region is EU based on slug."""
    return slug.lower() in EU_REGION_SLUGS


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest DigitalOcean sizes and regions into database."""
    token = os.environ.get("DIGITALOCEAN_API_TOKEN")
    if not token:
        raise RuntimeError("DIGITALOCEAN_API_TOKEN environment variable is required for DigitalOcean ingestion")

    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning(
            "DigitalOcean only supports on-demand pricing, ignoring purchase_model", purchase_model=purchase_model
        )
        purchase = PurchaseModel.ON_DEMAND

    # Fetch regions and sizes
    regions_list = await _fetch_regions(token)
    sizes_list = await _fetch_sizes(token)

    logger.info(
        "Fetched DigitalOcean data",
        regions_count=len(regions_list),
        sizes_count=len(sizes_list),
    )

    # Ensure provider
    provider = await ensure_provider(
        db,
        slug="digitalocean",
        name="DigitalOcean",
        website="https://www.digitalocean.com",
        type=ProviderType.REGIONAL,
    )
    await db.flush()

    # Process regions - ingest ALL regions, mark EU status correctly
    region_by_slug = {}
    for reg in regions_list:
        slug = (reg.get("slug") or "").strip().lower()
        if not slug:
            continue

        is_eu = _is_eu_region(slug)
        # Only filter if explicitly requested (but default is to ingest all)
        if only_eu and not is_eu:
            continue

        name = reg.get("name") or slug
        country = REGION_COUNTRY_MAP.get(slug)

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

    # Process sizes (instance types)
    inserted_prices = 0
    instance_types_seen = set()

    for size in sizes_list:
        slug = (size.get("slug") or "").strip()
        if not slug:
            continue

        # Extract instance type details
        vcpu = size.get("vcpus") or 0
        memory_mb = size.get("memory") or 0
        ram_gb = float(memory_mb) / 1024.0 if memory_mb else 0.0

        # DigitalOcean sizes are typically x86_64
        arch = "x86_64"

        # Family can be inferred from slug (e.g., s-1vcpu-1gb -> general purpose)
        family = None
        if slug.startswith("s-"):
            family = "standard"
        elif slug.startswith("c-"):
            family = "cpu-optimized"
        elif slug.startswith("m-"):
            family = "memory-optimized"
        elif slug.startswith("g-"):
            family = "general-purpose"

        # Ensure instance type
        it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=slug,
            vcpu=vcpu,
            ram_gb=ram_gb,
            family=family,
            arch=arch,
        )
        await db.flush()
        instance_types_seen.add(slug)

        # Get available regions for this size
        available_regions = size.get("regions") or []

        # Extract pricing
        price_hourly_usd = _price_per_hour_usd(
            price_monthly=size.get("price_monthly"),
            price_hourly=size.get("price_hourly"),
        )

        if price_hourly_usd is None or price_hourly_usd <= 0:
            logger.warning("Skipping size with invalid pricing", size_slug=slug)
            continue

        # Convert USD to EUR (using get_rate_to_eur fallback if FX rates not in DB)
        rate_usd_eur = await get_rate_to_eur(db, "USD")
        price_eur_per_hour = to_eur(price_hourly_usd, rate_usd_eur)

        # Create price entries for each available region
        for region_slug in available_regions:
            region_slug_lower = region_slug.lower()
            if region_slug_lower not in region_by_slug:
                continue  # Skip regions not in our filtered list

            region = region_by_slug[region_slug_lower]

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
        "DigitalOcean ingestion completed",
        provider_slug="digitalocean",
        regions=len(region_by_slug),
        instance_types=len(instance_types_seen),
        price_rows_inserted=inserted_prices,
    )
