"""Aruba Cloud connector: fetch server configurations from Aruba Cloud REST API.

API docs: https://api.arubacloud.com/documentation
Auth: POST /Authentication/Login with username+password → AuthenticationToken
Server types: GET /Compute/ServerTypes → list of available flavors with specs and pricing
"""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.models.price import PurchaseModel
from app.models.provider import ProviderType
from app.services.ingestion import (
    append_price,
    ensure_instance_type,
    ensure_provider,
    ensure_region,
)
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# Aruba Cloud API endpoints per data center
# DC IDs: 1=ITBG-1, 2=ITBG-2, 3=ITBG-3, 4=CZ-01, 5=CZAH-01
ARUBA_DC_API_URLS: dict[int, str] = {
    1: "https://api.dc1.computing.cloud.it/WsEndUser/v2.9/WsEndUser.svc/json",
    2: "https://api.dc2.computing.cloud.it/WsEndUser/v2.9/WsEndUser.svc/json",
    3: "https://api.dc3.computing.cloud.it/WsEndUser/v2.9/WsEndUser.svc/json",
    4: "https://api.dc4.computing.cloud.it/WsEndUser/v2.9/WsEndUser.svc/json",
    5: "https://api.dc5.computing.cloud.it/WsEndUser/v2.9/WsEndUser.svc/json",
}

ARUBA_DC_REGIONS: dict[int, dict] = {
    1: {"slug": "itbg-dc1", "name": "Bergamo DC1", "country": "IT", "is_eu": True},
    2: {"slug": "itbg-dc2", "name": "Bergamo DC2", "country": "IT", "is_eu": True},
    3: {"slug": "itbg-dc3", "name": "Bergamo DC3", "country": "IT", "is_eu": True},
    4: {"slug": "cz-dc1", "name": "Czech Republic DC1", "country": "CZ", "is_eu": True},
    5: {"slug": "czah-dc1", "name": "Czech Republic AH DC1", "country": "CZ", "is_eu": True},
}


async def _get_auth_token(base_url: str, username: str, password: str) -> str | None:
    """Authenticate with Aruba Cloud API and return auth token."""
    payload = {
        "ApplicationId": 1,
        "Username": username,
        "Password": password,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{base_url}/Authentication/Login",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
        if data.get("Success"):
            return data.get("Value", {}).get("Token") or data.get("Value")
        logger.warning("Aruba auth failed", message=data.get("Message"))
        return None
    except Exception as e:
        logger.error("Aruba auth request failed", error=str(e))
        return None


async def _fetch_server_types(base_url: str, token: str) -> list[dict]:
    """Fetch available server configurations from Aruba Cloud API."""
    payload = {"ApplicationId": 1, "Token": token}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{base_url}/Compute/GetHardwareProfiles",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
        if data.get("Success"):
            return data.get("Value") or []
        logger.warning("Aruba server types fetch failed", message=data.get("Message"))
        return []
    except Exception as e:
        logger.error("Aruba server types request failed", error=str(e))
        return []


async def _fetch_pricing(base_url: str, token: str) -> list[dict]:
    """Fetch Aruba Cloud pricing list."""
    payload = {"ApplicationId": 1, "Token": token}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{base_url}/Compute/GetPriceList",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
        if data.get("Success"):
            return data.get("Value") or []
    except Exception as e:
        logger.debug("Aruba pricing fetch failed", error=str(e))
    return []


def _build_price_map(price_list: list[dict]) -> dict[str, float]:
    """Build a map from hardware profile name → hourly price (EUR)."""
    price_map: dict[str, float] = {}
    for item in price_list:
        name = item.get("Name") or item.get("ResourceName") or ""
        # Prices are typically monthly; convert to hourly (/730)
        monthly = item.get("PriceMonthly") or item.get("Price") or 0.0
        hourly = item.get("PriceHourly") or item.get("HourlyPrice") or (float(monthly) / 730.0)
        if name:
            price_map[name] = float(hourly)
    return price_map


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest Aruba Cloud server configurations from the Aruba Cloud REST API.

    Requires ARUBA_USERNAME and ARUBA_PASSWORD environment variables.
    Set ARUBA_DC_ID to specify which data center to query (default: 1).
    """
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning(
            "Aruba only supports on-demand pricing, ignoring purchase_model",
            purchase_model=purchase_model,
        )
        purchase = PurchaseModel.ON_DEMAND

    if not settings.aruba_username or not settings.aruba_password:
        logger.error(
            "Aruba ingestion skipped: ARUBA_USERNAME and ARUBA_PASSWORD are not set. "
            "Use your Aruba Cloud console credentials."
        )
        return

    provider = await ensure_provider(
        db,
        slug="aruba",
        name="Aruba Cloud",
        website="https://www.arubacloud.com",
        type=ProviderType.EU,
    )
    await db.flush()

    dc_id = settings.aruba_dc_id
    base_url = ARUBA_DC_API_URLS.get(dc_id)
    region_meta = ARUBA_DC_REGIONS.get(dc_id)
    if not base_url or not region_meta:
        logger.error("Aruba: unknown DC ID", dc_id=dc_id)
        return
    if only_eu and not region_meta["is_eu"]:
        logger.info("Aruba: skipping non-EU DC", dc_id=dc_id)
        return

    # Authenticate
    token = await _get_auth_token(base_url, settings.aruba_username, settings.aruba_password)
    if not token:
        logger.error("Aruba: authentication failed")
        return

    region = await ensure_region(
        db,
        provider_id=provider.id,
        slug=region_meta["slug"],
        name=region_meta["name"],
        country=region_meta["country"],
        is_eu=region_meta["is_eu"],
    )
    await db.flush()

    # Fetch server types and pricing
    server_types = await _fetch_server_types(base_url, token)
    price_list = await _fetch_pricing(base_url, token)
    price_map = _build_price_map(price_list)

    logger.info(
        "Aruba API data fetched",
        dc_id=dc_id,
        server_types=len(server_types),
        price_entries=len(price_map),
    )

    inserted_prices = 0
    for item in server_types:
        name = item.get("Name") or item.get("ResourceName") or ""
        if not name:
            continue

        vcpu = item.get("CpuCount") or item.get("Cpu") or item.get("vCpuCount") or 0
        ram_mb = item.get("RamCount") or item.get("Ram") or item.get("MemoryMb") or 0
        ram_gb = float(ram_mb) / 1024.0 if ram_mb > 0 else 0.0
        disk_gb = item.get("DiskCount") or item.get("Disk") or item.get("StorageGb") or None

        if vcpu <= 0 or ram_gb <= 0:
            continue

        price_eur = price_map.get(name, 0.0)

        it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=name,
            vcpu=int(vcpu),
            ram_gb=float(ram_gb),
            family="cloud-server",
            arch="x86_64",
            disk_gb=float(disk_gb) if disk_gb else None,
        )
        await db.flush()

        if price_eur > 0:
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

    logger.info(
        "Aruba ingestion completed",
        provider_slug="aruba",
        dc_id=dc_id,
        instance_types=len(server_types),
        price_rows_inserted=inserted_prices,
    )
