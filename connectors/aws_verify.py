"""Verify AWS ingested prices match the Price List API (requires AWS credentials)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur, to_eur
from app.core.logging import get_logger
from app.models import InstanceType, Price, Provider, Region
from app.models.price import PurchaseModel
from connectors.aws import (
    AWS_PRICING_REGION,
    AWS_REGIONS,
    SERVICE_CODE_EC2,
    _extract_ondemand_price_usd,
    _fetch_ec2_ondemand_for_location,
    _get_pricing_client,
)

logger = get_logger(__name__)

RELTOL = 0.02
SAMPLE_REGION = "eu-west-1"
SAMPLE_TYPES = ["t3.micro", "t3.small", "t3.medium", "t3.large"]


@dataclass
class VerificationResult:
    passed: bool
    message: str
    details: dict[str, Any] | None = None


def _has_aws_creds() -> bool:
    return bool(os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"))


async def verify_ingested_prices(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
    fail_on_mismatch: bool = False,
) -> list[VerificationResult]:
    results: list[VerificationResult] = []
    if not _has_aws_creds():
        results.append(VerificationResult(passed=True, message="Skipped: AWS credentials not set"))
        return results

    if PurchaseModel(purchase_model) != PurchaseModel.ON_DEMAND:
        results.append(VerificationResult(passed=True, message="AWS verify only supports on_demand"))
        return results

    spec = next((s for s in AWS_REGIONS if s.slug == SAMPLE_REGION), None)
    if not spec or (only_eu and not spec.is_eu):
        results.append(VerificationResult(passed=True, message="AWS eu-west-1 not in scope"))
        return results

    try:
        client = _get_pricing_client()
        api_results = _fetch_ec2_ondemand_for_location(client, spec.location_name)
    except Exception as e:
        results.append(VerificationResult(passed=False, message=f"AWS Price API failed: {e}"))
        return results

    # Use first-seen price per instance type — matches append_price idempotency (first wins per hour)
    api_prices: dict[str, float] = {}
    for attrs, price_usd in api_results:
        itype = (attrs.get("instanceType") or "").strip()
        if itype in SAMPLE_TYPES and itype not in api_prices:
            api_prices[itype] = price_usd

    rate_usd_eur = await get_rate_to_eur(db, "USD")
    provider = (await db.execute(select(Provider).where(Provider.slug == "aws").limit(1))).scalars().first()
    if not provider:
        results.append(VerificationResult(passed=False, message="AWS provider not found"))
        return results

    region = (await db.execute(select(Region).where(Region.provider_id == provider.id).where(Region.slug == SAMPLE_REGION).limit(1))).scalars().first()
    if not region:
        results.append(VerificationResult(passed=False, message="AWS eu-west-1 region not found"))
        return results

    for itype in SAMPLE_TYPES:
        if itype not in api_prices:
            continue
        expected = to_eur(api_prices[itype], rate_usd_eur)
        it = (await db.execute(select(InstanceType).where(InstanceType.provider_id == provider.id).where(InstanceType.name == itype).limit(1))).scalars().first()
        if not it:
            results.append(VerificationResult(passed=False, message=f"AWS {itype}: not in DB", details={"expected": expected}))
            continue
        pr = (await db.execute(select(Price).where(Price.instance_type_id == it.id).where(Price.region_id == region.id).where(Price.purchase_model == PurchaseModel.ON_DEMAND).limit(1))).scalars().first()
        if not pr:
            results.append(VerificationResult(passed=False, message=f"AWS {itype}: no price", details={"expected": expected}))
            continue
        stored = pr.price_eur_per_hour or pr.price_per_hour
        diff = abs(stored - expected) / expected if expected > 0 else 0
        if diff > RELTOL:
            results.append(VerificationResult(passed=False, message=f"AWS {itype}: mismatch", details={"expected": expected, "stored": stored}))
        else:
            results.append(VerificationResult(passed=True, message=f"AWS {itype}: OK ({stored:.4f} EUR/hr)"))

    if not results:
        results.append(VerificationResult(passed=True, message="AWS: no overlapping types to verify"))

    if fail_on_mismatch and any(not r.passed for r in results):
        raise RuntimeError("AWS verification failed: " + "; ".join(r.message for r in results if not r.passed))
    return results
