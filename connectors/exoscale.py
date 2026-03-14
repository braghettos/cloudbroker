"""Exoscale connector: fetch instance types and pricing from Exoscale API v2.

API docs: https://openapi.exoscale.com/
Auth: HMAC-SHA256 (required; register at https://portal.exoscale.com/u/{org}/iam/api-keys)
Instance type listing: GET /instance-type (per-zone)
Pricing: included in instance type response as `unit-price` field (CHF/hr or EUR/hr)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

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

EXOSCALE_API_BASE = "https://api.exoscale.com/v2"

# Exoscale zones with their region metadata
# Zone list from: https://www.exoscale.com/datacenters/
EXOSCALE_ZONES = [
    {"slug": "ch-gva-2", "name": "Geneva 2", "country": "CH", "is_eu": False, "zone": "ch-gva-2"},
    {"slug": "ch-gva-3", "name": "Geneva 3", "country": "CH", "is_eu": False, "zone": "ch-gva-2"},
    {"slug": "at-vie-1", "name": "Vienna 1", "country": "AT", "is_eu": True, "zone": "at-vie-1"},
    {"slug": "at-vie-2", "name": "Vienna 2", "country": "AT", "is_eu": True, "zone": "at-vie-1"},
    {"slug": "de-muc-1", "name": "Munich 1", "country": "DE", "is_eu": True, "zone": "de-muc-1"},
    {"slug": "bg-sof-1", "name": "Sofia 1", "country": "BG", "is_eu": True, "zone": "bg-sof-1"},
]


def _exoscale_auth_headers(api_key: str, api_secret: str, method: str, path: str, body: bytes = b"") -> dict:
    """Generate Exoscale API v2 HMAC-SHA256 authentication headers."""
    timestamp = str(int(time.time()))
    body_hash = hashlib.sha256(body).hexdigest() if body else ""
    message = f"{method}\n{path}\n\n{timestamp}\n{body_hash}"
    signature = base64.b64encode(
        hmac.new(api_secret.encode(), message.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "X-Exo-Api-Key": api_key,
        "X-Exo-Timestamp": timestamp,
        "X-Exo-Signature": signature,
        "Content-Type": "application/json",
    }


async def _fetch_instance_types(zone: str) -> list[dict]:
    """Fetch all instance types for a zone from the Exoscale API.

    Requires EXOSCALE_API_KEY and EXOSCALE_API_SECRET.
    """
    if not settings.exoscale_api_key or not settings.exoscale_api_secret:
        raise RuntimeError(
            "EXOSCALE_API_KEY and EXOSCALE_API_SECRET are required. "
            "Generate them at https://portal.exoscale.com/u/{org}/iam/api-keys"
        )

    path = f"/instance-type"
    url = f"{EXOSCALE_API_BASE}{path}"
    headers = _exoscale_auth_headers(
        settings.exoscale_api_key, settings.exoscale_api_secret, "GET", path
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url, headers=headers, params={"zone": zone})
        r.raise_for_status()
        data = r.json()

    return data.get("instance-types") or []


def _parse_family(name: str) -> str:
    """Infer instance family from Exoscale instance type name."""
    n = name.lower()
    if n.startswith("gpu"):
        return "gpu"
    if n.startswith("cpu"):
        return "cpu"
    if n.startswith("memory"):
        return "memory"
    if n.startswith("storage"):
        return "storage"
    return "standard"


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest Exoscale instance types and pricing from the Exoscale API v2.

    Requires EXOSCALE_API_KEY and EXOSCALE_API_SECRET environment variables.
    """
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning(
            "Exoscale only supports on-demand pricing, ignoring purchase_model",
            purchase_model=purchase_model,
        )
        purchase = PurchaseModel.ON_DEMAND

    if not settings.exoscale_api_key or not settings.exoscale_api_secret:
        logger.error(
            "Exoscale ingestion skipped: EXOSCALE_API_KEY and EXOSCALE_API_SECRET not set. "
            "Generate API keys at https://portal.exoscale.com/u/{org}/iam/api-keys"
        )
        return

    provider = await ensure_provider(
        db,
        slug="exoscale",
        name="Exoscale",
        website="https://www.exoscale.com",
        type=ProviderType.EU,
    )
    await db.flush()

    zones_to_ingest = [z for z in EXOSCALE_ZONES if not only_eu or z["is_eu"]]

    # Fetch instance types once per unique API zone (zones share the same instance type catalog)
    seen_api_zones: dict[str, list[dict]] = {}
    for zone_meta in zones_to_ingest:
        api_zone = zone_meta["zone"]
        if api_zone not in seen_api_zones:
            try:
                types = await _fetch_instance_types(api_zone)
                seen_api_zones[api_zone] = types
                logger.info("Exoscale instance types fetched", zone=api_zone, count=len(types))
            except Exception as e:
                logger.error("Exoscale API fetch failed", zone=api_zone, error=str(e))
                seen_api_zones[api_zone] = []

    total_types = 0
    inserted_prices = 0

    for zone_meta in zones_to_ingest:
        region = await ensure_region(
            db,
            provider_id=provider.id,
            slug=zone_meta["slug"],
            name=zone_meta["name"],
            country=zone_meta["country"],
            is_eu=zone_meta["is_eu"],
        )
        await db.flush()

        api_types = seen_api_zones.get(zone_meta["zone"], [])
        for item in api_types:
            name = item.get("name") or item.get("id") or ""
            if not name:
                continue

            vcpu = item.get("cpus") or item.get("cpu-count") or 0
            # memory-bytes → GB
            memory_bytes = item.get("memory") or item.get("memory-bytes") or 0
            ram_gb = memory_bytes / (1024 ** 3) if memory_bytes > 0 else 0.0
            disk_bytes = item.get("disk") or item.get("disk-bytes") or 0
            disk_gb = disk_bytes / (1024 ** 3) if disk_bytes > 0 else None

            if vcpu <= 0 or ram_gb <= 0:
                continue

            # Price: Exoscale returns price in CHF/month in their API
            # Convert to EUR/hr using CHF→EUR ≈ 1.05 exchange rate, /730 hr/month
            # The API returns `unit-price` as the hourly cost in CHF
            unit_price_chf = (
                item.get("unit-price")
                or item.get("price")
                or 0.0
            )
            price_chf_per_hr = float(unit_price_chf)
            # CHF is close to EUR (roughly 1:1 within ±5%). Use source_currency to mark.
            price_eur_per_hr = price_chf_per_hr * 0.95  # approximate CHF→EUR

            gpu_count = item.get("gpu-count") or item.get("gpus") or None

            it = await ensure_instance_type(
                db,
                provider_id=provider.id,
                name=name,
                vcpu=int(vcpu),
                ram_gb=float(ram_gb),
                family=_parse_family(name),
                arch="x86_64",
                disk_gb=disk_gb,
                gpu_count=int(gpu_count) if gpu_count else None,
            )
            await db.flush()
            total_types += 1

            if price_chf_per_hr > 0:
                p = await append_price(
                    db,
                    instance_type_id=it.id,
                    region_id=region.id,
                    purchase_model=purchase,
                    currency="CHF",
                    price_per_hour=price_chf_per_hr,
                    price_eur_per_hour=price_eur_per_hr,
                    source_currency="CHF",
                )
                if p:
                    inserted_prices += 1

    logger.info(
        "Exoscale ingestion completed",
        provider_slug="exoscale",
        regions=len(zones_to_ingest),
        instance_types=total_types,
        price_rows_inserted=inserted_prices,
    )
