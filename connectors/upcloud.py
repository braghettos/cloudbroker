"""UpCloud connector: fetch plans and pricing via API, ingest into DB."""

from __future__ import annotations

import asyncio
import base64
import os
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

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

UPCLOUD_BASE = "https://api.upcloud.com/1.2"

# Zone prefix -> country code
ZONE_PREFIX_TO_COUNTRY = {
    "fi": "FI",
    "de": "DE",
    "nl": "NL",
    "uk": "GB",
    "sg": "SG",
    "us": "US",
}

EU_COUNTRIES = {"FI", "DE", "NL"}

UPCLOUD_TIMEOUT = 30.0
UPCLOUD_FETCH_RETRIES = 3


def _auth_headers_from_token(token: str) -> dict[str, str]:
    """Build HTTP Bearer auth header for API token."""
    return {"Authorization": f"Bearer {token.strip()}"}


def _auth_headers_from_credentials(username: str, password: str) -> dict[str, str]:
    """Build HTTP Basic auth header from username/password."""
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


async def _fetch_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs,
) -> dict:
    """Fetch with retries for transient failures."""
    last_err = None
    for attempt in range(1, UPCLOUD_FETCH_RETRIES + 1):
        try:
            r = await client.request(method, url, **kwargs)
            r.raise_for_status()
            return r.json()
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout) as e:
            last_err = e
            if attempt < UPCLOUD_FETCH_RETRIES:
                logger.warning(
                    "UpCloud fetch attempt %s/%s failed: %s, retrying in 5s...",
                    attempt,
                    UPCLOUD_FETCH_RETRIES,
                    e,
                )
                await asyncio.sleep(5)
    raise last_err


async def _fetch_plans(auth_headers: dict[str, str]) -> list[dict]:
    """Fetch available plans from UpCloud API."""
    async with httpx.AsyncClient(timeout=UPCLOUD_TIMEOUT) as client:
        data = await _fetch_with_retry(
            client,
            "GET",
            f"{UPCLOUD_BASE}/plan",
            headers=auth_headers,
        )
    plans_obj = data.get("plans") or {}
    plans_list = plans_obj.get("plan") if isinstance(plans_obj, dict) else []
    return plans_list if isinstance(plans_list, list) else []


async def _fetch_zones(auth_headers: dict[str, str]) -> list[dict]:
    """Fetch available zones from UpCloud API."""
    async with httpx.AsyncClient(timeout=UPCLOUD_TIMEOUT) as client:
        data = await _fetch_with_retry(
            client,
            "GET",
            f"{UPCLOUD_BASE}/zone",
            headers=auth_headers,
        )
    zones_obj = data.get("zones") or {}
    zones_list = zones_obj.get("zone") if isinstance(zones_obj, dict) else []
    return zones_list if isinstance(zones_list, list) else []


async def _fetch_prices(auth_headers: dict[str, str]) -> list[dict]:
    """Fetch pricing by zone from UpCloud API."""
    async with httpx.AsyncClient(timeout=UPCLOUD_TIMEOUT) as client:
        data = await _fetch_with_retry(
            client,
            "GET",
            f"{UPCLOUD_BASE}/price",
            headers=auth_headers,
        )
    prices_obj = data.get("prices") or {}
    zones_list = prices_obj.get("zone") if isinstance(prices_obj, dict) else []
    return zones_list if isinstance(zones_list, list) else []


def _zone_info(zone_id: str, description: str) -> dict:
    """Derive region metadata from zone id (e.g. fi-hel1 -> Helsinki, FI, EU)."""
    zone_id = (zone_id or "").strip().lower()
    prefix = zone_id.split("-")[0] if zone_id else ""
    country = ZONE_PREFIX_TO_COUNTRY.get(prefix, prefix.upper() if prefix else "FI")
    is_eu = country in EU_COUNTRIES
    name = (description or zone_id).strip() or zone_id
    return {"name": name, "country": country, "is_eu": is_eu}


def _get_auth_headers() -> dict[str, str]:
    """
    Get auth headers: prefer UPCLOUD_API_TOKEN (Bearer), else UPCLOUD_USERNAME + UPCLOUD_PASSWORD (Basic).
    """
    token = (os.environ.get("UPCLOUD_API_TOKEN") or "").strip()
    if token:
        return _auth_headers_from_token(token)
    username = (os.environ.get("UPCLOUD_USERNAME") or "").strip()
    password = (os.environ.get("UPCLOUD_PASSWORD") or "").strip()
    if username and password:
        return _auth_headers_from_credentials(username, password)
    raise RuntimeError(
        "UpCloud ingestion requires either UPCLOUD_API_TOKEN (recommended, create at hub.upcloud.com/account/api-tokens) "
        "or UPCLOUD_USERNAME and UPCLOUD_PASSWORD"
    )


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest UpCloud plans and pricing into database."""
    auth_headers = _get_auth_headers()

    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning(
            "UpCloud only supports on-demand pricing, ignoring purchase_model",
            purchase_model=purchase_model,
        )
        purchase = PurchaseModel.ON_DEMAND

    plans_list = await _fetch_plans(auth_headers)
    zones_list = await _fetch_zones(auth_headers)
    prices_list = await _fetch_prices(auth_headers)

    provider = await ensure_provider(
        db,
        slug="upcloud",
        name="UpCloud",
        website="https://upcloud.com",
        type=ProviderType.EU,
    )
    await db.flush()

    region_by_slug = {}
    for z in zones_list:
        zone_id = (z.get("id") or "").strip().lower()
        if not zone_id:
            continue
        info = _zone_info(zone_id, z.get("description") or "")
        if only_eu and not info["is_eu"]:
            continue
        region = await ensure_region(
            db,
            provider_id=provider.id,
            slug=zone_id,
            name=info["name"],
            country=info["country"],
            is_eu=info["is_eu"],
        )
        region_by_slug[zone_id] = region
    await db.flush()

    inserted_prices = 0
    instance_types_seen = set()

    for plan in plans_list:
        name = (plan.get("name") or "").strip()
        if not name:
            continue

        cpu = int(plan.get("core_number") or 0)
        ram_mb = int(plan.get("memory_amount") or 0)
        ram_gb = float(ram_mb) / 1024.0 if ram_mb else 0.0

        it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=name,
            vcpu=cpu,
            ram_gb=ram_gb,
            family="cloud",
            arch="x86_64",
        )
        await db.flush()
        instance_types_seen.add(name)

        plan_price_key = f"server_plan_{name}"
        for zone_data in prices_list:
            zone_id = (zone_data.get("name") or "").strip().lower()
            if not zone_id or zone_id not in region_by_slug:
                continue
            price_val = zone_data.get(plan_price_key)
            if price_val is None:
                continue
            if isinstance(price_val, dict):
                price_val = price_val.get("price")
            try:
                eur_per_hour = float(Decimal(str(price_val)))
            except (TypeError, ValueError):
                continue
            if eur_per_hour <= 0:
                continue
            region = region_by_slug[zone_id]
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
        "UpCloud ingestion completed",
        provider_slug="upcloud",
        regions=len(region_by_slug),
        instance_types=len(instance_types_seen),
        price_rows_inserted=inserted_prices,
    )
