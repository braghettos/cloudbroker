"""Verify Open Telekom Cloud ingested prices match the Price API (public, no auth)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import InstanceType, Price, Provider, Region
from app.models.price import PurchaseModel
from connectors.open_telekom_cloud import LINUX_OS_UNITS, OTC_REGIONS, _fetch_all_ecs_prices, _parse_price

logger = get_logger(__name__)

RELTOL = 0.02
SAMPLE_REGION = "eu-de"
MAX_SAMPLES = 5


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
        results.append(VerificationResult(passed=True, message="OTC verify only supports on_demand"))
        return results

    reg_spec = next((r for r in OTC_REGIONS if r["slug"] == SAMPLE_REGION), None)
    if not reg_spec or (only_eu and not reg_spec.get("is_eu")):
        results.append(VerificationResult(passed=True, message="OTC eu-de not in scope"))
        return results

    try:
        ecs_list = await _fetch_all_ecs_prices(SAMPLE_REGION)
    except Exception as e:
        results.append(VerificationResult(passed=False, message=f"OTC API fetch failed: {e}"))
        return results

    api_prices: dict[str, float] = {}
    for rec in ecs_list:
        if (rec.get("osUnit") or "").strip() not in LINUX_OS_UNITS:
            continue
        flavor = (rec.get("opiFlavour") or "").strip()
        if not flavor:
            continue
        price_val = _parse_price(rec.get("priceAmount") or "")
        if price_val is not None and price_val > 0:
            api_prices[flavor] = price_val

    provider = (await db.execute(select(Provider).where(Provider.slug == "open_telekom_cloud").limit(1))).scalars().first()
    if not provider:
        results.append(VerificationResult(passed=False, message="OTC provider not found in DB"))
        return results

    region = (await db.execute(select(Region).where(Region.provider_id == provider.id).where(Region.slug == SAMPLE_REGION).limit(1))).scalars().first()
    if not region:
        results.append(VerificationResult(passed=False, message="OTC eu-de region not found"))
        return results

    sampled = 0
    for flavor, expected in list(api_prices.items())[:MAX_SAMPLES * 3]:
        if sampled >= MAX_SAMPLES:
            break
        it = (await db.execute(select(InstanceType).where(InstanceType.provider_id == provider.id).where(InstanceType.name == flavor).limit(1))).scalars().first()
        if not it:
            continue
        price_row = (await db.execute(select(Price).where(Price.instance_type_id == it.id).where(Price.region_id == region.id).where(Price.purchase_model == PurchaseModel.ON_DEMAND).limit(1))).scalars().first()
        if not price_row:
            results.append(VerificationResult(passed=False, message=f"OTC {flavor}: no price in DB", details={"expected": expected}))
            sampled += 1
            continue
        stored = price_row.price_eur_per_hour or price_row.price_per_hour
        diff = abs(stored - expected) / expected if expected > 0 else 0
        if diff > RELTOL:
            results.append(VerificationResult(passed=False, message=f"OTC {flavor}: price mismatch", details={"expected": expected, "stored": stored, "diff_pct": round(diff * 100, 2)}))
        else:
            results.append(VerificationResult(passed=True, message=f"OTC {flavor}: OK ({stored:.4f} EUR/hr)"))
        sampled += 1

    if not results:
        results.append(VerificationResult(passed=True, message="OTC: no overlapping flavors to verify (DB may be empty)"))

    if fail_on_mismatch and any(not r.passed for r in results):
        raise RuntimeError("OTC verification failed: " + "; ".join(r.message for r in results if not r.passed))
    return results
