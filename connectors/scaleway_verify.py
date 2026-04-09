"""Verify Scaleway ingested prices match the Instance API (requires SCW keys)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import InstanceType, Price, Provider, Region
from app.models.price import PurchaseModel
from connectors.scaleway import SCALEWAY_BASE, ZONE_MAP, _price_per_hour_eur

logger = get_logger(__name__)

RELTOL = 0.02
SAMPLE_ZONE = "fr-par-1"
SAMPLE_TYPES = ["DEV1-S", "GP1-XS"]


@dataclass
class VerificationResult:
    passed: bool
    message: str
    details: dict[str, Any] | None = None


async def verify_ingested_prices(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
    fail_on_mismatch: bool = False,
) -> list[VerificationResult]:
    results: list[VerificationResult] = []
    token = os.environ.get("SCALEWAY_SECRET_KEY") or os.environ.get("SCW_SECRET_KEY")
    if not token:
        results.append(VerificationResult(passed=True, message="Skipped: SCALEWAY_SECRET_KEY not set"))
        return results

    if PurchaseModel(purchase_model) != PurchaseModel.ON_DEMAND:
        results.append(VerificationResult(passed=True, message="Scaleway verify only supports on_demand"))
        return results

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"{SCALEWAY_BASE}/zones/{SAMPLE_ZONE}/products/servers",
                headers={"X-Auth-Token": token, "Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        results.append(VerificationResult(passed=False, message=f"Scaleway API failed: {e}"))
        return results

    servers = data.get("servers")
    if isinstance(servers, dict):
        servers = [{"_instance_type_name": k, **v} for k, v in servers.items() if isinstance(v, dict)]
    if not isinstance(servers, list):
        servers = []

    api_prices: dict[str, float] = {}
    for s in servers:
        name = s.get("_instance_type_name") or (s.get("alt_names") or [None])[0]
        if not name:
            continue
        name = str(name).strip()
        if name not in SAMPLE_TYPES:
            continue
        price = _price_per_hour_eur(s.get("price_per_hour"))
        if price and price > 0:
            api_prices[name] = price

    provider = (await db.execute(select(Provider).where(Provider.slug == "scaleway").limit(1))).scalars().first()
    if not provider:
        results.append(VerificationResult(passed=False, message="Scaleway provider not found"))
        return results

    zone_info = ZONE_MAP.get(SAMPLE_ZONE, {})
    region_slug = zone_info.get("slug", SAMPLE_ZONE)
    region = (
        (
            await db.execute(
                select(Region).where(Region.provider_id == provider.id).where(Region.slug == region_slug).limit(1)
            )
        )
        .scalars()
        .first()
    )
    if not region:
        region = (await db.execute(select(Region).where(Region.provider_id == provider.id).limit(1))).scalars().first()
    if not region:
        results.append(VerificationResult(passed=False, message="Scaleway no region in DB"))
        return results

    for name in SAMPLE_TYPES:
        if name not in api_prices:
            continue
        expected = api_prices[name]
        it = (
            (
                await db.execute(
                    select(InstanceType)
                    .where(InstanceType.provider_id == provider.id)
                    .where(InstanceType.name == name)
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if not it:
            results.append(
                VerificationResult(passed=False, message=f"Scaleway {name}: not in DB", details={"expected": expected})
            )
            continue
        pr = (
            (
                await db.execute(
                    select(Price)
                    .where(Price.instance_type_id == it.id)
                    .where(Price.region_id == region.id)
                    .where(Price.purchase_model == PurchaseModel.ON_DEMAND)
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if not pr:
            results.append(
                VerificationResult(passed=False, message=f"Scaleway {name}: no price", details={"expected": expected})
            )
            continue
        stored = pr.price_eur_per_hour or pr.price_per_hour
        diff = abs(stored - expected) / expected if expected > 0 else 0
        if diff > RELTOL:
            results.append(
                VerificationResult(
                    passed=False, message=f"Scaleway {name}: mismatch", details={"expected": expected, "stored": stored}
                )
            )
        else:
            results.append(VerificationResult(passed=True, message=f"Scaleway {name}: OK ({stored:.4f} EUR/hr)"))

    if not results:
        results.append(VerificationResult(passed=True, message="Scaleway: no overlapping types to verify"))

    if fail_on_mismatch and any(not r.passed for r in results):
        raise RuntimeError("Scaleway verification failed: " + "; ".join(r.message for r in results if not r.passed))
    return results
