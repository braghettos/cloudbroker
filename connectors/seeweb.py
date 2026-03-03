"""Seeweb Cloud connector: fetch plans and pricing via API, ingest into DB."""

from __future__ import annotations

import os
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Region
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

# Region slug -> (name, country, is_eu)
REGION_MAP = {
    "it-mi2": {"name": "Milano", "country": "IT", "is_eu": True},
    "it-fr2": {"name": "Frosinone", "country": "IT", "is_eu": True},
    "ch-lug1": {"name": "Lugano", "country": "CH", "is_eu": False},  # Switzerland
    "bg-sof1": {"name": "Sofia", "country": "BG", "is_eu": True},
}


async def _login(username: str, password: str) -> str:
    """Obtain JWT token from Seeweb login endpoint."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{SEEWEB_BASE}/login",
            json={"username": username, "password": password},
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
    token = data.get("token")
    if not token:
        raise RuntimeError("Seeweb login did not return a token")
    return token


async def _fetch_plans(token: str) -> list[dict]:
    """Fetch available plans from Seeweb API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{SEEWEB_BASE}/plans",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        data = r.json()
    plans = data.get("plans") or []
    if data.get("status") != "ok":
        logger.warning("Seeweb plans response status was not ok", status=data.get("status"))
    return plans


def _price_per_hour_eur(plan: dict) -> float | None:
    """Extract hourly price in EUR from plan."""
    try:
        hourly = plan.get("hourly_price")
        if hourly is not None:
            return float(Decimal(str(hourly)))
        monthly = plan.get("montly_price")  # Note: Seeweb typo "montly"
        if monthly is not None:
            return float(Decimal(str(monthly)) / 730)
    except (TypeError, ValueError):
        pass
    return None


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest Seeweb Cloud Server plans and pricing into database."""
    username = os.environ.get("SEEWEB_USERNAME")
    password = os.environ.get("SEEWEB_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "SEEWEB_USERNAME and SEEWEB_PASSWORD environment variables are required for Seeweb ingestion"
        )

    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning(
            "Seeweb only supports on-demand pricing, ignoring purchase_model",
            purchase_model=purchase_model,
        )
        purchase = PurchaseModel.ON_DEMAND

    token = await _login(username, password)
    plans_list = await _fetch_plans(token)

    provider = await ensure_provider(
        db,
        slug="seeweb",
        name="Seeweb",
        website="https://www.seeweb.it",
        type=ProviderType.EU,
    )
    await db.flush()

    region_by_slug = {}
    for slug, info in REGION_MAP.items():
        is_eu = info["is_eu"]
        if only_eu and not is_eu:
            continue
        region = await ensure_region(
            db,
            provider_id=provider.id,
            slug=slug,
            name=info["name"],
            country=info["country"],
            is_eu=is_eu,
        )
        region_by_slug[slug] = region
    await db.flush()

    inserted_prices = 0
    instance_types_seen = set()

    for plan in plans_list:
        name = (plan.get("name") or "").strip()
        if not name:
            continue
        if not plan.get("available", True):
            continue
        # Skip GPU plans for now (focus on compute)
        gpu = plan.get("gpu") or "0"
        if str(gpu) != "0":
            continue

        cpu = int(plan.get("cpu") or 0)
        ram_mb = int(plan.get("ram") or 0)
        ram_gb = float(ram_mb) / 1024.0 if ram_mb else 0.0
        disk_gb = int(plan.get("disk") or 0)

        eur_per_hour = _price_per_hour_eur(plan)
        if eur_per_hour is None or eur_per_hour <= 0:
            logger.warning("Skipping plan with invalid pricing", plan=name)
            continue

        it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=name,
            vcpu=cpu,
            ram_gb=ram_gb,
            family="ecs",
            arch="x86_64",
        )
        await db.flush()
        instance_types_seen.add(name)

        available_regions = plan.get("available_regions") or []
        for reg_info in available_regions:
            location = (reg_info.get("location") or "").strip().lower()
            if not location or location not in region_by_slug:
                continue
            region = region_by_slug[location]
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
        "Seeweb ingestion completed",
        provider_slug="seeweb",
        regions=len(region_by_slug),
        instance_types=len(instance_types_seen),
        price_rows_inserted=inserted_prices,
    )
