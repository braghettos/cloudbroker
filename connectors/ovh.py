"""OVH connector: fetch flavors and pricing, ingest into DB.

Data sources:
    1. **Project Flavor API** (authenticated):
       ``GET /cloud/project/{id}/flavor`` → specs, region, plan codes.
    2. **Public Catalog** (no auth):
       ``GET /order/catalog/public/cloud?ovhSubsidiary=FR`` → ex-VAT hourly prices.
"""

from __future__ import annotations

import hashlib
import os
import time
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
    # Try full prefix first (e.g. "EU-WEST-PAR" → prefix "EUWESTPAR")
    # then strip trailing digits (e.g. "GRA11" → "GRA")
    alpha = "".join(c for c in slug if c.isalpha()).upper()
    for prefix in (alpha, alpha.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")):
        pass
    # Progressively shorten until we find a match
    for length in range(len(alpha), 0, -1):
        candidate = alpha[:length]
        if candidate in _DC_META:
            city, country, is_eu = _DC_META[candidate]
            return f"{city} ({slug})", country, is_eu
    return slug, None, False


# ---------------------------------------------------------------------------
# OVH API helpers
# ---------------------------------------------------------------------------


def _sign_request(
    method: str,
    url: str,
    body: str,
    application_secret: str,
    consumer_key: str,
    timestamp: int,
) -> str:
    """OVH signature: ``$1$`` + SHA1(AS+CK+METHOD+URL+BODY+TSTAMP).

    ``URL`` must be the **full URL** (scheme + host + path + query string).
    """
    signature_string = f"{application_secret}+{consumer_key}+{method}+{url}+{body}+{timestamp}"
    signature = hashlib.sha1(signature_string.encode()).hexdigest()
    return f"$1${signature}"


async def _make_ovh_request(
    client: httpx.AsyncClient,
    method: str,
    endpoint: str,
    application_key: str,
    application_secret: str,
    consumer_key: str,
    body: str = "",
) -> httpx.Response:
    """Make authenticated OVH API request."""
    url = f"{OVH_BASE}{endpoint}"
    timestamp = int(time.time())
    signature = _sign_request(method, url, body, application_secret, consumer_key, timestamp)

    headers = {
        "X-Ovh-Application": application_key,
        "X-Ovh-Consumer": consumer_key,
        "X-Ovh-Signature": signature,
        "X-Ovh-Timestamp": str(timestamp),
        "Content-Type": "application/json",
    }

    if method == "GET":
        return await client.get(url, headers=headers)
    if method == "POST":
        return await client.post(url, headers=headers, content=body)
    raise ValueError(f"Unsupported method: {method}")


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------


async def _fetch_all_flavors(
    application_key: str,
    application_secret: str,
    consumer_key: str,
    service_name: str,
) -> list[dict]:
    """Fetch all project flavors across all regions in a single API call."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        endpoint = f"/cloud/project/{service_name}/flavor"
        response = await _make_ovh_request(
            client,
            "GET",
            endpoint,
            application_key,
            application_secret,
            consumer_key,
        )
        response.raise_for_status()
        return response.json()


async def _fetch_public_catalog_prices() -> dict[str, float]:
    """Fetch **ex-VAT** hourly prices from the OVH public catalog.

    Returns a mapping ``{ plan_base_name: price_eur_per_hour }``
    (e.g. ``{"b2-7": 0.05448, "b2-15": 0.10752, ...}``).
    """
    url = f"{OVH_BASE}/order/catalog/public/cloud?ovhSubsidiary=FR"
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    price_map: dict[str, float] = {}
    for addon in data.get("addons", []):
        plan_code = addon.get("planCode", "")
        if ".consumption" not in plan_code:
            continue
        base_name = plan_code.replace(".consumption", "")
        # Skip option add-ons (e.g. "b2-7.option.dc-adp.consumption")
        if ".option." in base_name:
            continue
        for pricing in addon.get("pricings", []):
            raw_price = pricing.get("price")
            raw_tax = pricing.get("tax", 0)
            if raw_price is not None and raw_price > 0:
                # OVH catalog prices are in microcents (EUR × 10^8).
                ex_vat = (raw_price - raw_tax) / 100_000_000
                price_map[base_name] = round(ex_vat, 6)
                break

    return price_map


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


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

    # --- 1. Ensure provider ---
    provider = await ensure_provider(
        db,
        slug="ovh",
        name="OVHcloud",
        website="https://www.ovhcloud.com",
        type=ProviderType.REGIONAL,
    )
    await db.flush()

    # --- 2. Fetch pricing from public catalog (no auth) ---
    try:
        catalog_prices = await _fetch_public_catalog_prices()
        logger.info("Fetched public catalog prices", count=len(catalog_prices))
    except Exception as e:
        logger.error("Failed to fetch OVH public catalog", error=str(e))
        raise

    # --- 3. Fetch all project flavors (authenticated) ---
    try:
        all_flavors = await _fetch_all_flavors(
            application_key,
            application_secret,
            consumer_key,
            service_name,
        )
        logger.info("Fetched project flavors", count=len(all_flavors))
    except Exception as e:
        logger.error("Failed to fetch project flavors", error=str(e))
        raise

    # --- 4. Discover regions from the flavor list ---
    region_slugs: set[str] = set()
    for f in all_flavors:
        r = f.get("region")
        if r:
            region_slugs.add(r)

    region_by_slug = {}
    for slug in sorted(region_slugs):
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

    # --- 6. Process flavors ---
    inserted_prices = 0
    instance_types_seen: set[str] = set()
    no_price: set[str] = set()

    purchase = PurchaseModel(purchase_model)

    for flavor in all_flavors:
        flavor_region = flavor.get("region", "")
        region = region_by_slug.get(flavor_region)
        if region is None:
            continue

        flavor_name = flavor.get("name", "")
        if not flavor_name:
            continue

        # Specs
        vcpu = int(flavor.get("vcpus") or 0)
        # The /cloud/project/{id}/flavor endpoint returns RAM in GB (not MB).
        ram_gb = float(flavor.get("ram") or 0)

        arch = flavor.get("osType") or "x86_64"
        if arch not in ("x86_64", "arm64"):
            arch = "x86_64"

        family = _infer_family(flavor_name)

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

        # Price lookup: match by flavor name (plan codes use the same name)
        price_eur = catalog_prices.get(flavor_name)
        if price_eur is None or price_eur <= 0:
            if flavor_name not in no_price:
                no_price.add(flavor_name)
            continue

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

    if no_price:
        logger.warning("Flavors without catalog pricing", count=len(no_price), samples=sorted(no_price)[:10])

    logger.info(
        "OVH ingestion completed",
        instance_types=len(instance_types_seen),
        price_rows_inserted=inserted_prices,
        provider_slug="ovh",
        regions=len(region_by_slug),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _infer_family(flavor_name: str) -> str:
    """Infer instance family from OVH flavor name prefix."""
    prefix = flavor_name.split("-")[0].lower() if "-" in flavor_name else flavor_name.lower()
    families = {
        "b2": "general-purpose",
        "b3": "general-purpose",
        "c2": "compute-optimized",
        "c3": "compute-optimized",
        "r2": "memory-optimized",
        "r3": "memory-optimized",
        "d2": "general-purpose",
        "i1": "io-optimized",
        "t1": "general-purpose",
        "t2": "general-purpose",
        "g": "gpu",
    }
    return families.get(prefix, "general-purpose")
