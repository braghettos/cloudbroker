"""Hetzner Cloud connector: fetch locations and server types, ingest into DB."""

from __future__ import annotations

import os
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.price import PurchaseModel
from app.services.ingestion import (
    ensure_instance_type,
    ensure_provider,
    ensure_region,
    append_price,
)

logger = get_logger(__name__)

HETZNER_BASE = "https://api.hetzner.cloud/v1"
EU_COUNTRY_CODES = {"DE", "FI", "US"}  # fsn1,nbg1=DE, hel1=FI; ash,hil=US - only EU locations have is_eu
# Actually EU = DE, FI for Hetzner; US (ash, hil) and Singapore are non-EU
EU_LOCATION_NAMES = {"fsn1", "nbg1", "hel1"}  # Falkenstein, Nuremberg, Helsinki


async def _fetch_locations(token: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{HETZNER_BASE}/locations",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 50},
        )
        r.raise_for_status()
        data = r.json()
    return data.get("locations") or []


async def _fetch_server_types(token: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{HETZNER_BASE}/server_types",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 50},
        )
        r.raise_for_status()
        data = r.json()
    return data.get("server_types") or []


def _price_per_hour_eur(price_entry: dict) -> float | None:
    """Extract hourly price in EUR from Hetzner price object. API may give net/hourly or net/monthly."""
    try:
        # Prefer hourly
        hourly = price_entry.get("price_hourly")
        if hourly is not None:
            net = hourly.get("net") if isinstance(hourly, dict) else None
            if net is not None:
                return float(Decimal(str(net)))
        # Fallback: monthly -> hourly (730 hours per month)
        monthly = price_entry.get("price_monthly")
        if monthly is not None:
            net = monthly.get("net") if isinstance(monthly, dict) else None
            if net is not None:
                return float(Decimal(str(net)) / 730)
    except (TypeError, ValueError):
        pass
    return None


def _arch(raw: str | None) -> str:
    if not raw:
        return "x86_64"
    return "arm64" if raw.lower() == "arm" else "x86_64"


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    token = os.environ.get("HETZNER_API_TOKEN")
    if not token:
        raise RuntimeError("HETZNER_API_TOKEN environment variable is required for Hetzner ingestion")

    purchase = PurchaseModel(purchase_model)
    locations_list = await _fetch_locations(token)
    server_types_list = await _fetch_server_types(token)

    # Build location slug -> (name, country, is_eu)
    loc_by_slug = {}
    for loc in locations_list:
        slug = (loc.get("name") or "").strip().lower()
        if not slug:
            continue
        country = (loc.get("country") or "")[:2].upper()
        is_eu = country in ("DE", "FI", "AT", "FR", "NL", "PL", "ES", "IT", "IE", "BE", "SE") or slug in EU_LOCATION_NAMES
        if only_eu and not is_eu:
            continue
        loc_by_slug[slug] = {
            "name": loc.get("description") or slug,
            "country": country or None,
            "is_eu": is_eu,
        }

    provider = await ensure_provider(
        db,
        slug="hetzner",
        name="Hetzner Cloud",
        website="https://www.hetzner.com/cloud",
    )
    await db.flush()

    # Create regions
    for slug, info in loc_by_slug.items():
        await ensure_region(
            db,
            provider_id=provider.id,
            slug=slug,
            name=info["name"],
            country=info["country"],
            is_eu=info["is_eu"],
        )
    await db.flush()

    # Fetch regions from DB to get ids by slug
    from sqlalchemy import select
    from app.models import Region

    regions_stmt = select(Region).where(Region.provider_id == provider.id)
    regions_result = await db.execute(regions_stmt)
    region_by_slug = {r.slug: r for r in regions_result.scalars().all()}

    inserted_prices = 0
    for st in server_types_list:
        name = (st.get("name") or "").strip()
        if not name:
            continue
        cores = st.get("cores") or 0
        memory_gb = st.get("memory") or 0
        if isinstance(memory_gb, (int, float)):
            pass
        else:
            memory_gb = float(memory_gb) if memory_gb else 0
        arch = _arch(st.get("architecture"))
        it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=name,
            vcpu=cores,
            ram_gb=float(memory_gb),
            family=st.get("description") or None,
            arch=arch,
        )
        await db.flush()

        prices = st.get("prices") or []
        for price_entry in prices:
            location_name = (price_entry.get("location") or "").strip().lower()
            if location_name not in region_by_slug:
                continue
            region = region_by_slug[location_name]
            eur_per_hour = _price_per_hour_eur(price_entry)
            if eur_per_hour is None or eur_per_hour < 0:
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
        "Hetzner ingestion completed",
        regions=len(region_by_slug),
        server_types=len(server_types_list),
        price_rows_inserted=inserted_prices,
    )
