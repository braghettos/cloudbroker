"""OVH connector: fetch flavors and pricing, ingest into DB."""

from __future__ import annotations

import hashlib
import os
import time
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

OVH_BASE = "https://api.ovh.com/1.0"

# OVH EU regions
# Based on OVH Public Cloud regions
OVH_EU_REGIONS = [
    {"slug": "GRA11", "name": "Gravelines", "country": "FR", "is_eu": True},
    {"slug": "SBG5", "name": "Strasbourg", "country": "FR", "is_eu": True},
    {"slug": "BHS5", "name": "Beauharnois", "country": "CA", "is_eu": False},  # Canada
    {"slug": "WAW1", "name": "Warsaw", "country": "PL", "is_eu": True},
    {"slug": "DE1", "name": "Limburg", "country": "DE", "is_eu": True},
    {"slug": "UK1", "name": "London", "country": "GB", "is_eu": True},
]


def _sign_request(
    method: str,
    url: str,
    body: str,
    application_secret: str,
    consumer_key: str,
    timestamp: int,
) -> str:
    """Sign OVH API request according to OVH signature algorithm.

    OVH signature format: $1$ + SHA1(AS + "+" + CK + "+" + METHOD + "+" + QUERY + "+" + BODY + "+" + TSTAMP)
    Where QUERY is the full URL path + query string (without domain)
    """
    # Extract path + query from URL
    from urllib.parse import urlparse
    parsed = urlparse(url)
    query = parsed.path
    if parsed.query:
        query += "?" + parsed.query

    # Build signature string
    signature_string = (
        f"{application_secret}+{consumer_key}+{method}+{query}+{body}+{timestamp}"
    )
    signature = hashlib.sha1(signature_string.encode()).hexdigest()
    return f"$1${signature}"


async def _make_ovh_request(
    client: httpx.AsyncClient,
    method: str,
    endpoint: str,
    application_key: str,
    application_secret: str,
    consumer_key: str,
    service_name: str,
    body: str = "",
) -> httpx.Response:
    """Make authenticated OVH API request."""
    url = f"{OVH_BASE}{endpoint}"
    timestamp = int(time.time())

    # Sign the request
    signature = _sign_request(
        method, url, body, application_secret, consumer_key, timestamp
    )

    headers = {
        "X-Ovh-Application": application_key,
        "X-Ovh-Consumer": consumer_key,
        "X-Ovh-Signature": signature,
        "X-Ovh-Timestamp": str(timestamp),
        "Content-Type": "application/json",
    }

    if method == "GET":
        response = await client.get(url, headers=headers)
    elif method == "POST":
        response = await client.post(url, headers=headers, content=body)
    else:
        raise ValueError(f"Unsupported method: {method}")

    return response


async def _fetch_flavors(
    application_key: str,
    application_secret: str,
    consumer_key: str,
    service_name: str,
    region: str,
) -> list[dict]:
    """Fetch available flavors (instance types) for a region."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        endpoint = f"/cloud/project/{service_name}/region/{region}/flavor"
        response = await _make_ovh_request(
            client,
            "GET",
            endpoint,
            application_key,
            application_secret,
            consumer_key,
            service_name,
        )
        response.raise_for_status()
        return response.json()


async def _fetch_pricing(
    application_key: str,
    application_secret: str,
    consumer_key: str,
    service_name: str,
    flavor_id: str,
    region: str,
) -> dict | None:
    """Fetch pricing for a specific flavor in a region."""
    # OVH pricing might be in a different endpoint
    # For now, we'll try to get it from the flavor details or a pricing endpoint
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Try flavor details endpoint first
        endpoint = f"/cloud/project/{service_name}/region/{region}/flavor/{flavor_id}"
        try:
            response = await _make_ovh_request(
                client,
                "GET",
                endpoint,
                application_key,
                application_secret,
                consumer_key,
                service_name,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError:
            logger.warning("Could not fetch pricing for flavor", flavor_id=flavor_id, region=region)
            return None


async def ingest(
    db: AsyncSession,
    only_eu: bool = False,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest OVH instance types and pricing into database."""
    application_key = os.environ.get("OVH_APPLICATION_KEY") or os.environ.get("OVH_AK")
    application_secret = os.environ.get("OVH_APPLICATION_SECRET") or os.environ.get("OVH_AS")
    consumer_key = os.environ.get("OVH_CONSUMER_KEY") or os.environ.get("OVH_CK")
    service_name = os.environ.get("OVH_SERVICE_NAME") or os.environ.get("OVH_PROJECT_ID")

    if not all([application_key, application_secret, consumer_key, service_name]):
        raise RuntimeError(
            "OVH_APPLICATION_KEY, OVH_APPLICATION_SECRET, OVH_CONSUMER_KEY, and OVH_SERVICE_NAME "
            "environment variables are required for OVH ingestion"
        )

    # Ensure provider
    provider = await ensure_provider(
        db,
        slug="ovh",
        name="OVHcloud",
        website="https://www.ovhcloud.com",
        provider_type=ProviderType.REGIONAL,
    )
    await db.flush()

    # Ensure regions
    region_by_slug = {}
    for region_info in OVH_EU_REGIONS:
        if only_eu and not region_info["is_eu"]:
            continue

        region = await ensure_region(
            db,
            provider_id=provider.id,
            slug=region_info["slug"],
            name=region_info["name"],
            country=region_info["country"],
            is_eu=region_info["is_eu"],
        )
        region_by_slug[region_info["slug"]] = region

    await db.flush()
    logger.info("Created/updated regions", count=len(region_by_slug))

    # Ensure FX rate for EUR exists (OVH prices are in EUR)
    await get_rate_to_eur(db, "EUR")

    # Process flavors per region
    inserted_prices = 0
    instance_types_seen = set()

    for region_slug, region in region_by_slug.items():
        try:
            flavors = await _fetch_flavors(
                application_key,
                application_secret,
                consumer_key,
                service_name,
                region_slug,
            )
            logger.info("Fetched flavors for region", region=region_slug, count=len(flavors))

            for flavor in flavors:
                flavor_id = flavor.get("id") or flavor.get("name")
                if not flavor_id:
                    continue

                # Extract instance specs
                vcpu = int(flavor.get("vcpus") or flavor.get("vcpu") or flavor.get("cores") or 0)
                ram_mb = flavor.get("ram") or 0
                ram_gb = float(ram_mb) / 1024.0 if ram_mb else 0.0

                # OVH instances are typically x86_64
                arch = flavor.get("osType") or "x86_64"
                if arch not in ["x86_64", "arm64"]:
                    arch = "x86_64"

                # Infer family from flavor name/type
                flavor_name = flavor.get("name") or flavor_id
                family = _infer_family(flavor_name)

                # Ensure instance type
                it = await ensure_instance_type(
                    db,
                    provider_id=provider.id,
                    name=flavor_name,
                    vcpu=vcpu,
                    ram_gb=ram_gb,
                    family=family,
                    arch=arch,
                )
                await db.flush()
                instance_types_seen.add(flavor_name)

                # Get pricing
                pricing = await _fetch_pricing(
                    application_key,
                    application_secret,
                    consumer_key,
                    service_name,
                    flavor_id,
                    region_slug,
                )

                # Extract hourly price (OVH uses EUR)
                price_hourly_eur = None
                if pricing:
                    # Pricing structure varies - try common fields
                    price_hourly_eur = (
                        pricing.get("hourlyPrice")
                        or pricing.get("price")
                        or pricing.get("hourly")
                        or pricing.get("pricePerHour")
                    )

                if price_hourly_eur is None:
                    # Try to get from flavor directly
                    price_hourly_eur = (
                        flavor.get("hourlyPrice")
                        or flavor.get("price")
                        or flavor.get("hourly")
                    )

                if price_hourly_eur is None or price_hourly_eur <= 0:
                    logger.warning("Skipping flavor with invalid pricing", flavor=flavor_name, region=region_slug)
                    continue

                price_hourly_eur = float(Decimal(str(price_hourly_eur)))

                # Convert purchase model
                purchase = PurchaseModel(purchase_model)

                # Append price
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
            logger.warning("Error fetching flavors for region", region=region_slug, error=str(e))
            continue

    logger.info(
        "OVH ingestion completed",
        instance_types=len(instance_types_seen),
        price_rows_inserted=inserted_prices,
        provider_slug="ovh",
        regions=len(region_by_slug),
    )


def _infer_family(flavor_name: str) -> str:
    """Infer instance family from OVH flavor name."""
    name_upper = flavor_name.upper()

    if "B" in name_upper and ("1" in name_upper or "2" in name_upper):
        return "general-purpose"
    elif "C" in name_upper and ("1" in name_upper or "2" in name_upper):
        return "compute-optimized"
    elif "R" in name_upper or "MEMORY" in name_upper:
        return "memory-optimized"
    elif "GPU" in name_upper or "G" in name_upper:
        return "gpu"
    elif "I" in name_upper:
        return "io-optimized"
    else:
        return "general-purpose"
