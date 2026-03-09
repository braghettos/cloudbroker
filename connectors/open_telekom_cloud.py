"""Open Telekom Cloud connector: fetch ECS pricing via public Price API, ingest into DB."""

from __future__ import annotations

import asyncio
import re
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

OTC_PRICE_API = "https://calculator.otc-service.com/en/open-telekom-price-api/"

# Regions from OTC Price API (eu-de, eu-nl, eu-ch2)
OTC_REGIONS = [
    {"slug": "eu-de", "name": "Germany", "country": "DE", "is_eu": True},
    {"slug": "eu-nl", "name": "Netherlands", "country": "NL", "is_eu": True},
    {"slug": "eu-ch2", "name": "Switzerland", "country": "CH", "is_eu": False},
]

# Use Open Linux (standard Linux) pricing; skip licensed OS (Oracle, SUSE, SAP, Windows)
LINUX_OS_UNITS = {"Open Linux", "Linux"}

OTC_TIMEOUT = 60.0
OTC_FETCH_RETRIES = 3


async def _fetch_prices(region: str, limit_from: int = 0, limit_max: int = 500) -> dict:
    """Fetch ECS pricing from OTC Price API (public, no auth)."""
    async with httpx.AsyncClient(timeout=OTC_TIMEOUT) as client:
        last_err = None
        for attempt in range(1, OTC_FETCH_RETRIES + 1):
            try:
                r = await client.get(
                    OTC_PRICE_API,
                    params={
                        "pt": "OTC",
                        "sn": "ecs",
                        "rn": region,
                        "lm": limit_max,
                        "lf": limit_from,
                    },
                )
                r.raise_for_status()
                return r.json()
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout) as e:
                last_err = e
                if attempt < OTC_FETCH_RETRIES:
                    logger.warning(
                        "OTC price fetch attempt %s/%s failed: %s, retrying in 5s...",
                        attempt,
                        OTC_FETCH_RETRIES,
                        e,
                    )
                    await asyncio.sleep(5)
        raise last_err


def _parse_price(price_str: str) -> float | None:
    """Parse '0.051175 EUR' -> 0.051175."""
    if not price_str or not isinstance(price_str, str):
        return None
    m = re.match(r"([\d.]+)", price_str.strip())
    if not m:
        return None
    try:
        return float(Decimal(m.group(1)))
    except (TypeError, ValueError):
        return None


def _parse_ram_gb(ram_str: str) -> float:
    """Parse '1 GiB' -> 1.0, '4 GiB' -> 4.0."""
    if not ram_str or not isinstance(ram_str, str):
        return 0.0
    m = re.match(r"([\d.]+)\s*(?:GiB|GB)?", ram_str.strip(), re.I)
    if not m:
        return 0.0
    try:
        return float(Decimal(m.group(1)))
    except (TypeError, ValueError):
        return 0.0


async def _fetch_all_ecs_prices(region: str) -> list[dict]:
    """Fetch all ECS price records for a region (paginate if needed)."""
    all_records = []
    limit_from = 0
    limit_max = 500
    while True:
        data = await _fetch_prices(region, limit_from=limit_from, limit_max=limit_max)
        resp = data.get("response") or {}
        if resp.get("code") != "Success":
            logger.warning("OTC price API error", region=region, response=resp)
            break
        result = resp.get("result") or {}
        ecs_list = result.get("ecs") if isinstance(result, dict) else []
        if not ecs_list:
            break
        all_records.extend(ecs_list)
        stats = resp.get("stats") or {}
        current_page = int(stats.get("currentPage") or 1)
        max_pages = int(stats.get("maxPages") or 1)
        if current_page >= max_pages:
            break
        limit_from = current_page * limit_max
    return all_records


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest Open Telekom Cloud ECS flavors and pricing into database."""
    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning(
            "Open Telekom Cloud only supports on-demand pricing, ignoring purchase_model",
            purchase_model=purchase_model,
        )
        purchase = PurchaseModel.ON_DEMAND

    provider = await ensure_provider(
        db,
        slug="open_telekom_cloud",
        name="Open Telekom Cloud",
        website="https://open-telekom-cloud.com",
        type=ProviderType.EU,
    )
    await db.flush()

    region_by_slug = {}
    for reg in OTC_REGIONS:
        if only_eu and not reg["is_eu"]:
            continue
        region = await ensure_region(
            db,
            provider_id=provider.id,
            slug=reg["slug"],
            name=reg["name"],
            country=reg["country"],
            is_eu=reg["is_eu"],
        )
        region_by_slug[reg["slug"]] = region
    await db.flush()

    inserted_prices = 0
    instance_types_seen = set()

    for reg_slug in region_by_slug:
        records = await _fetch_all_ecs_prices(reg_slug)
        region = region_by_slug[reg_slug]

        seen_flavor_in_region = set()
        for rec in records:
            os_unit = (rec.get("osUnit") or "").strip()
            if os_unit not in LINUX_OS_UNITS:
                continue

            flavor = (rec.get("opiFlavour") or "").strip()
            if not flavor or flavor in seen_flavor_in_region:
                continue
            seen_flavor_in_region.add(flavor)

            vcpu = int(rec.get("vCpu") or 0)
            ram_gb = _parse_ram_gb(rec.get("ram") or "")
            price_val = _parse_price(rec.get("priceAmount") or "")
            if price_val is None or price_val <= 0:
                continue

            it = await ensure_instance_type(
                db,
                provider_id=provider.id,
                name=flavor,
                vcpu=vcpu,
                ram_gb=ram_gb,
                family=rec.get("serviceType") or "ecs",
                arch="x86_64",
            )
            await db.flush()
            instance_types_seen.add(flavor)

            p = await append_price(
                db,
                instance_type_id=it.id,
                region_id=region.id,
                purchase_model=purchase,
                currency="EUR",
                price_per_hour=price_val,
                price_eur_per_hour=price_val,
            )
            if p:
                inserted_prices += 1

    logger.info(
        "Open Telekom Cloud ingestion completed",
        provider_slug="open_telekom_cloud",
        regions=len(region_by_slug),
        instance_types=len(instance_types_seen),
        price_rows_inserted=inserted_prices,
    )
