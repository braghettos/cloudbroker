"""Verify DigitalOcean ingested prices match the API (requires DIGITALOCEAN_API_TOKEN)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur, to_eur
from app.core.logging import get_logger
from app.models import InstanceType, Price, Provider, Region
from app.models.price import PurchaseModel
from connectors.digitalocean import DO_BASE, EU_REGION_SLUGS, _price_per_hour_usd

logger = get_logger(__name__)

RELTOL = 0.02
SAMPLE_SLUGS = ["s-1vcpu-1gb", "s-2vcpu-2gb"]


@dataclass
class VerificationResult:
    passed: bool
    message: str
    details: dict[str, Any] | None = None


async def _fetch_sizes(token: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{DO_BASE}/sizes", headers={"Authorization": f"Bearer {token}"}, params={"per_page": 200})
        r.raise_for_status()
        return r.json().get("sizes") or []


async def verify_ingested_prices(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
    fail_on_mismatch: bool = False,
) -> list[VerificationResult]:
    results: list[VerificationResult] = []
    token = os.environ.get("DIGITALOCEAN_API_TOKEN")
    if not token:
        results.append(VerificationResult(passed=True, message="Skipped: DIGITALOCEAN_API_TOKEN not set"))
        return results

    if PurchaseModel(purchase_model) != PurchaseModel.ON_DEMAND:
        results.append(VerificationResult(passed=True, message="DO verify only supports on_demand"))
        return results

    try:
        sizes = await _fetch_sizes(token)
    except Exception as e:
        results.append(VerificationResult(passed=False, message=f"DO API fetch failed: {e}"))
        return results

    rate_usd_eur = await get_rate_to_eur(db, "USD")
    api_prices: dict[str, float] = {}
    for s in sizes:
        slug = (s.get("slug") or "").strip()
        if slug not in SAMPLE_SLUGS:
            continue
        regions = s.get("regions") or []
        if only_eu and not any(r in EU_REGION_SLUGS for r in regions):
            continue
        price_usd = _price_per_hour_usd(s.get("price_monthly"), s.get("price_hourly"))
        if price_usd and price_usd > 0:
            api_prices[slug] = to_eur(price_usd, rate_usd_eur)

    provider = (await db.execute(select(Provider).where(Provider.slug == "digitalocean").limit(1))).scalars().first()
    if not provider:
        results.append(VerificationResult(passed=False, message="DigitalOcean provider not found"))
        return results

    region_slug = "ams3" if only_eu else "nyc1"
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
        results.append(VerificationResult(passed=False, message="DigitalOcean no region in DB"))
        return results

    for slug in SAMPLE_SLUGS:
        if slug not in api_prices:
            continue
        expected = api_prices[slug]
        it = (
            (
                await db.execute(
                    select(InstanceType)
                    .where(InstanceType.provider_id == provider.id)
                    .where(InstanceType.name == slug)
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if not it:
            results.append(
                VerificationResult(passed=False, message=f"DO {slug}: not in DB", details={"expected": expected})
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
                VerificationResult(passed=False, message=f"DO {slug}: no price", details={"expected": expected})
            )
            continue
        stored = pr.price_eur_per_hour or pr.price_per_hour
        diff = abs(stored - expected) / expected if expected > 0 else 0
        if diff > RELTOL:
            results.append(
                VerificationResult(
                    passed=False, message=f"DO {slug}: mismatch", details={"expected": expected, "stored": stored}
                )
            )
        else:
            results.append(VerificationResult(passed=True, message=f"DO {slug}: OK ({stored:.4f} EUR/hr)"))

    if not results:
        results.append(VerificationResult(passed=True, message="DO: no overlapping sizes to verify"))

    if fail_on_mismatch and any(not r.passed for r in results):
        raise RuntimeError("DigitalOcean verification failed: " + "; ".join(r.message for r in results if not r.passed))
    return results
