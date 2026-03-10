"""Verify Seeweb ingested prices match the ECS API (requires SEEWEB_API_TOKEN)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import InstanceType, Price, Provider, Region
from app.models.price import PurchaseModel
from connectors.seeweb import _fetch_plans, _price_per_hour_eur

logger = get_logger(__name__)

RELTOL = 0.02
SAMPLE_NAMES = ["Cloud Server S", "Cloud Server M"]


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
    token = os.environ.get("SEEWEB_API_TOKEN")
    if not token:
        results.append(VerificationResult(passed=True, message="Skipped: SEEWEB_API_TOKEN not set"))
        return results

    if PurchaseModel(purchase_model) != PurchaseModel.ON_DEMAND:
        results.append(VerificationResult(passed=True, message="Seeweb verify only supports on_demand"))
        return results

    try:
        plans = await _fetch_plans(api_token=token)
    except Exception as e:
        results.append(VerificationResult(passed=False, message=f"Seeweb API failed: {e}"))
        return results

    api_prices: dict[str, float] = {}
    for p in plans:
        name = (p.get("name") or "").strip()
        if name in SAMPLE_NAMES:
            price = _price_per_hour_eur(p)
            if price and price > 0:
                api_prices[name] = price

    provider = (await db.execute(select(Provider).where(Provider.slug == "seeweb").limit(1))).scalars().first()
    if not provider:
        results.append(VerificationResult(passed=False, message="Seeweb provider not found"))
        return results

    region = (await db.execute(select(Region).where(Region.provider_id == provider.id).limit(1))).scalars().first()
    if not region:
        results.append(VerificationResult(passed=False, message="Seeweb no region in DB"))
        return results

    for name in SAMPLE_NAMES:
        if name not in api_prices:
            continue
        expected = api_prices[name]
        it = (await db.execute(select(InstanceType).where(InstanceType.provider_id == provider.id).where(InstanceType.name == name).limit(1))).scalars().first()
        if not it:
            results.append(VerificationResult(passed=False, message=f"Seeweb {name}: not in DB", details={"expected": expected}))
            continue
        pr = (await db.execute(select(Price).where(Price.instance_type_id == it.id).where(Price.region_id == region.id).where(Price.purchase_model == PurchaseModel.ON_DEMAND).limit(1))).scalars().first()
        if not pr:
            results.append(VerificationResult(passed=False, message=f"Seeweb {name}: no price", details={"expected": expected}))
            continue
        stored = pr.price_eur_per_hour or pr.price_per_hour
        diff = abs(stored - expected) / expected if expected > 0 else 0
        if diff > RELTOL:
            results.append(VerificationResult(passed=False, message=f"Seeweb {name}: mismatch", details={"expected": expected, "stored": stored}))
        else:
            results.append(VerificationResult(passed=True, message=f"Seeweb {name}: OK ({stored:.4f} EUR/hr)"))

    if not results:
        results.append(VerificationResult(passed=True, message="Seeweb: no overlapping plans to verify"))

    if fail_on_mismatch and any(not r.passed for r in results):
        raise RuntimeError("Seeweb verification failed: " + "; ".join(r.message for r in results if not r.passed))
    return results
