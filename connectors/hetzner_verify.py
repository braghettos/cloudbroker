"""Verify Hetzner ingested prices match the Cloud API.

Re-fetches server_types from the Hetzner API, compares with stored DB values,
and reports mismatches. Use after ingestion to double-check stored prices.

Note: Ingestion adds primary IPv4 add-on (0.0008 EUR/hr) to API base price so
displayed prices match the website (server+IPv4 default).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import InstanceType, Price, Provider, Region
from app.models.price import PurchaseModel
from connectors.hetzner import HETZNER_BASE, HETZNER_PRIMARY_IPV4_EUR_PER_HOUR, _price_per_hour_eur

logger = get_logger(__name__)

RELTOL = 0.01  # 1% tolerance for FX/rounding differences

# Sample instance types to verify (mix of shared and dedicated)
SAMPLE_TYPES = ["cx23", "cpx11", "cpx21", "ccx13", "cx33"]


async def _fetch_server_types(token: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.get(
            f"{HETZNER_BASE}/server_types",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 100},
        )
        r.raise_for_status()
        return r.json().get("server_types") or []


@dataclass
class VerificationResult:
    """Result of a single verification check."""

    passed: bool
    message: str
    details: dict[str, Any] | None = None


async def verify_ingested_prices(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
    fail_on_mismatch: bool = False,
) -> list[VerificationResult]:
    """
    Verify ingested Hetzner prices match the Cloud API.

    Returns list of VerificationResult. If fail_on_mismatch and any result failed,
    raises RuntimeError after collecting all results.
    """
    results: list[VerificationResult] = []
    token = os.environ.get("HETZNER_API_TOKEN")
    if not token:
        results.append(
            VerificationResult(
                passed=False,
                message="HETZNER_API_TOKEN not set; cannot verify Hetzner prices",
            )
        )
        return results

    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        results.append(VerificationResult(passed=True, message="Hetzner verify only supports on_demand; skipping"))
        return results

    server_types = await _fetch_server_types(token)
    if not server_types:
        results.append(VerificationResult(passed=False, message="No server types returned from Hetzner API"))
        return results

    provider = (await db.execute(select(Provider).where(Provider.slug == "hetzner").limit(1))).scalars().first()
    if not provider:
        results.append(VerificationResult(passed=False, message="Hetzner provider not found in DB"))
        if fail_on_mismatch:
            raise RuntimeError("Hetzner verification failed: provider not found")
        return results

    regions_stmt = select(Region).where(Region.provider_id == provider.id)
    if only_eu:
        regions_stmt = regions_stmt.where(Region.is_eu.is_(True))
    regions = list((await db.execute(regions_stmt)).scalars().all())
    region_by_slug = {r.slug: r for r in regions}

    st_by_name = {s.get("name", "").lower(): s for s in server_types if s.get("name")}

    for type_name in SAMPLE_TYPES:
        st = st_by_name.get(type_name.lower())
        if not st:
            continue

        it = (
            (
                await db.execute(
                    select(InstanceType)
                    .where(InstanceType.provider_id == provider.id)
                    .where(InstanceType.name == type_name)
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if not it:
            results.append(
                VerificationResult(
                    passed=False,
                    message=f"Hetzner {type_name}: instance type not in DB",
                )
            )
            continue

        api_prices: dict[str, float] = {}
        for pe in st.get("prices") or []:
            loc = (pe.get("location") or "").strip().lower()
            if loc not in region_by_slug:
                continue
            base = _price_per_hour_eur(pe)
            if base is not None and base >= 0:
                # Add IPv4 add-on to match ingestion (website shows server+IPv4)
                api_prices[loc] = float(Decimal(str(base)) + HETZNER_PRIMARY_IPV4_EUR_PER_HOUR)

        if not api_prices:
            results.append(
                VerificationResult(
                    passed=True,
                    message=f"Hetzner {type_name}: no EU prices in API (skipping)",
                )
            )
            continue

        # Get stored prices for this instance type
        prices_stmt = (
            select(Price, Region)
            .join(Region, Price.region_id == Region.id)
            .where(Price.instance_type_id == it.id)
            .where(Price.purchase_model == purchase)
        )
        stored = list((await db.execute(prices_stmt)).all())

        mismatches = []
        for price_row, region in stored:
            if region.slug not in api_prices:
                continue
            expected = api_prices[region.slug]
            stored_val = price_row.price_eur_per_hour or price_row.price_per_hour
            if stored_val is None:
                mismatches.append(
                    {"region": region.slug, "expected": expected, "stored": None, "reason": "no stored price"}
                )
                continue
            diff_pct = abs(stored_val - expected) / expected if expected > 0 else 0
            if diff_pct > RELTOL:
                mismatches.append(
                    {
                        "region": region.slug,
                        "expected_eur_hr": round(expected, 6),
                        "stored_eur_hr": round(stored_val, 6),
                        "diff_pct": round(diff_pct * 100, 2),
                    }
                )

        if mismatches:
            results.append(
                VerificationResult(
                    passed=False,
                    message=f"Hetzner {type_name}: price mismatch vs API",
                    details={"mismatches": mismatches},
                )
            )
        else:
            min_api = min(api_prices.values())
            results.append(
                VerificationResult(
                    passed=True,
                    message=f"Hetzner {type_name}: OK (API min {min_api:.4f} EUR/hr)",
                )
            )

    if fail_on_mismatch and any(not r.passed for r in results):
        raise RuntimeError("Hetzner verification failed: " + "; ".join(r.message for r in results if not r.passed))
    return results
