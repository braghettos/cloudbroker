"""Verify OVH ingested prices match the public catalog (no auth)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import InstanceType, Price, Provider
from app.models.price import PurchaseModel
from connectors.ovh import _fetch_public_catalog_prices

logger = get_logger(__name__)

RELTOL = 0.02
SAMPLE_PLANS = ["b2-7", "b2-15"]


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
    if PurchaseModel(purchase_model) != PurchaseModel.ON_DEMAND:
        results.append(VerificationResult(passed=True, message="OVH verify only supports on_demand"))
        return results

    try:
        catalog = await _fetch_public_catalog_prices()
    except Exception as e:
        results.append(VerificationResult(passed=False, message=f"OVH public catalog fetch failed: {e}"))
        return results

    provider = (await db.execute(select(Provider).where(Provider.slug == "ovh").limit(1))).scalars().first()
    if not provider:
        results.append(VerificationResult(passed=False, message="OVH provider not found in DB"))
        return results

    for plan in SAMPLE_PLANS:
        if plan not in catalog:
            continue
        expected = catalog[plan]
        it = (
            (
                await db.execute(
                    select(InstanceType)
                    .where(InstanceType.provider_id == provider.id)
                    .where(InstanceType.name == plan)
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if not it:
            results.append(
                VerificationResult(
                    passed=False, message=f"OVH {plan}: instance type not in DB", details={"catalog_eur": expected}
                )
            )
            continue
        price_stmt = (
            select(Price)
            .where(Price.instance_type_id == it.id)
            .where(Price.purchase_model == PurchaseModel.ON_DEMAND)
            .limit(5)
        )
        price_rows = list((await db.execute(price_stmt)).scalars().all())
        if not price_rows:
            results.append(
                VerificationResult(passed=False, message=f"OVH {plan}: no price in DB", details={"expected": expected})
            )
            continue
        stored = price_rows[0].price_eur_per_hour or price_rows[0].price_per_hour
        diff = abs(stored - expected) / expected if expected > 0 else 0
        if diff > RELTOL:
            results.append(
                VerificationResult(
                    passed=False,
                    message=f"OVH {plan}: price mismatch",
                    details={"expected": expected, "stored": stored, "diff_pct": round(diff * 100, 2)},
                )
            )
        else:
            results.append(VerificationResult(passed=True, message=f"OVH {plan}: OK ({stored:.4f} EUR/hr)"))

    if fail_on_mismatch and any(not r.passed for r in results):
        raise RuntimeError("OVH verification failed: " + "; ".join(r.message for r in results if not r.passed))
    return results
