"""Verify UpCloud ingested prices match the API (requires UPCLOUD_API_TOKEN or username/password)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import InstanceType, Price, Provider, Region
from app.models.price import PurchaseModel
from connectors.upcloud import _fetch_plans, _fetch_prices, _fetch_zones, _get_auth_headers

logger = get_logger(__name__)

RELTOL = 0.02
SAMPLE_PLANS = ["1xCPU-1GB", "2xCPU-4GB"]


@dataclass
class VerificationResult:
    passed: bool
    message: str
    details: dict[str, Any] | None = None


def _has_upcloud_creds() -> bool:
    return bool(os.environ.get("UPCLOUD_API_TOKEN") or (os.environ.get("UPCLOUD_USERNAME") and os.environ.get("UPCLOUD_PASSWORD")))


async def verify_ingested_prices(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
    fail_on_mismatch: bool = False,
) -> list[VerificationResult]:
    results: list[VerificationResult] = []
    if not _has_upcloud_creds():
        results.append(VerificationResult(passed=True, message="Skipped: UpCloud credentials not set"))
        return results

    if PurchaseModel(purchase_model) != PurchaseModel.ON_DEMAND:
        results.append(VerificationResult(passed=True, message="UpCloud verify only supports on_demand"))
        return results

    try:
        auth = _get_auth_headers()
        plans = await _fetch_plans(auth)
        prices = await _fetch_prices(auth)
    except Exception as e:
        results.append(VerificationResult(passed=False, message=f"UpCloud API failed: {e}"))
        return results

    price_by_plan: dict[str, float] = {}
    for z in prices:
        for plan in SAMPLE_PLANS:
            key = f"server_plan_{plan}"
            val = z.get(key)
            if val is None:
                continue
            if isinstance(val, dict):
                val = val.get("price")
            try:
                price_by_plan[plan] = float(val)
                break
            except (TypeError, ValueError):
                pass
        if len(price_by_plan) >= len(SAMPLE_PLANS):
            break

    provider = (await db.execute(select(Provider).where(Provider.slug == "upcloud").limit(1))).scalars().first()
    if not provider:
        results.append(VerificationResult(passed=False, message="UpCloud provider not found"))
        return results

    region = (await db.execute(select(Region).where(Region.provider_id == provider.id).limit(1))).scalars().first()
    if not region:
        results.append(VerificationResult(passed=False, message="UpCloud no region in DB"))
        return results

    for plan in SAMPLE_PLANS:
        if plan not in price_by_plan:
            continue
        # UpCloud API returns price in 0.01 EUR units; connector divides by 100 for eur_per_hour
        raw = price_by_plan[plan]
        expected = raw / 100.0
        it = (await db.execute(select(InstanceType).where(InstanceType.provider_id == provider.id).where(InstanceType.name == plan).limit(1))).scalars().first()
        if not it:
            results.append(VerificationResult(passed=False, message=f"UpCloud {plan}: not in DB", details={"expected": expected}))
            continue
        pr = (await db.execute(select(Price).where(Price.instance_type_id == it.id).where(Price.region_id == region.id).where(Price.purchase_model == PurchaseModel.ON_DEMAND).limit(1))).scalars().first()
        if not pr:
            results.append(VerificationResult(passed=False, message=f"UpCloud {plan}: no price", details={"expected": expected}))
            continue
        stored = pr.price_eur_per_hour or pr.price_per_hour
        diff = abs(stored - expected) / expected if expected > 0 else 0
        if diff > RELTOL:
            results.append(VerificationResult(passed=False, message=f"UpCloud {plan}: mismatch", details={"expected": expected, "stored": stored}))
        else:
            results.append(VerificationResult(passed=True, message=f"UpCloud {plan}: OK ({stored:.4f} EUR/hr)"))

    if not results:
        results.append(VerificationResult(passed=True, message="UpCloud: no overlapping plans to verify"))

    if fail_on_mismatch and any(not r.passed for r in results):
        raise RuntimeError("UpCloud verification failed: " + "; ".join(r.message for r in results if not r.passed))
    return results
