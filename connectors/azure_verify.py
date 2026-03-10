"""Verify Azure ingested prices match the Retail Prices API (public, no auth)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur, to_eur
from app.core.logging import get_logger
from app.models import InstanceType, Price, Provider, Region
from app.models.price import PurchaseModel
from app.services.ingestion import pick_one_per_logical_product
from connectors.azure import (
    AZURE_RETAIL_PRICES_URL,
    AZURE_REGIONS,
    _extract_hourly_price_usd,
    _fetch_retail_prices_page,
    _is_linux_product,
    _is_spot_vm_item,
    parse_azure_vm_specs,
)

logger = get_logger(__name__)

RELTOL = 0.02  # 2% for FX/rounding
SAMPLE_REGION = "westeurope"
SAMPLE_SKUS = ["Standard_B2s", "Standard_D2s_v3"]


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
    purchase = PurchaseModel(purchase_model)
    if purchase not in (PurchaseModel.ON_DEMAND, PurchaseModel.SPOT):
        results.append(VerificationResult(passed=True, message="Azure verify only supports on_demand/spot"))
        return results

    spec = next((s for s in AZURE_REGIONS if s.slug == SAMPLE_REGION), None)
    if not spec or (only_eu and not spec.is_eu):
        results.append(VerificationResult(passed=True, message="Azure westeurope not in scope"))
        return results

    rate_usd_eur = await get_rate_to_eur(db, "USD")
    async with httpx.AsyncClient(timeout=60.0) as client:
        all_candidates: list[dict] = []
        next_link = None
        while True:
            items, next_link = await _fetch_retail_prices_page(client, SAMPLE_REGION, next_page_link=next_link)
            for item in items:
                sku = (item.get("armSkuName") or item.get("skuName") or "").strip()
                if not sku or not sku.startswith("Standard_"):
                    continue
                is_spot = _is_spot_vm_item(item)
                if purchase == PurchaseModel.ON_DEMAND and is_spot:
                    continue
                if purchase == PurchaseModel.SPOT and not is_spot:
                    continue
                price_usd = _extract_hourly_price_usd(item)
                if price_usd is None or price_usd <= 0:
                    continue
                all_candidates.append(item)
            if not next_link:
                break

    canonical = pick_one_per_logical_product(
        all_candidates,
        group_key=lambda i: ((i.get("armSkuName") or i.get("skuName") or "").strip(), i.get("armRegionName", "")),
        sort_key=lambda i: (0 if _is_linux_product(i) else 1, i.get("skuId", ""), i.get("meterId", "")),
    )
    api_prices = {}
    for item in canonical:
        sku = (item.get("armSkuName") or item.get("skuName") or "").strip()
        price_usd = _extract_hourly_price_usd(item)
        if price_usd and sku in SAMPLE_SKUS:
            api_prices[sku] = to_eur(price_usd, rate_usd_eur)

    provider = (await db.execute(select(Provider).where(Provider.slug == "azure").limit(1))).scalars().first()
    if not provider:
        results.append(VerificationResult(passed=False, message="Azure provider not found in DB"))
        return results

    region = (await db.execute(select(Region).where(Region.provider_id == provider.id).where(Region.slug == SAMPLE_REGION).limit(1))).scalars().first()
    if not region:
        results.append(VerificationResult(passed=False, message="Azure westeurope region not found"))
        return results

    for sku in SAMPLE_SKUS:
        if sku not in api_prices:
            continue
        expected = api_prices[sku]
        it = (await db.execute(select(InstanceType).where(InstanceType.provider_id == provider.id).where(InstanceType.name == sku).limit(1))).scalars().first()
        if not it:
            results.append(VerificationResult(passed=False, message=f"Azure {sku}: instance type not in DB"))
            continue
        price_row = (await db.execute(select(Price).where(Price.instance_type_id == it.id).where(Price.region_id == region.id).where(Price.purchase_model == purchase).limit(1))).scalars().first()
        if not price_row:
            results.append(VerificationResult(passed=False, message=f"Azure {sku}: no price in DB", details={"expected_eur": expected}))
            continue
        stored = price_row.price_eur_per_hour or price_row.price_per_hour
        diff = abs(stored - expected) / expected if expected > 0 else 0
        if diff > RELTOL:
            results.append(VerificationResult(passed=False, message=f"Azure {sku}: price mismatch", details={"expected": expected, "stored": stored, "diff_pct": round(diff * 100, 2)}))
        else:
            results.append(VerificationResult(passed=True, message=f"Azure {sku}: OK ({stored:.4f} EUR/hr)"))

    if fail_on_mismatch and any(not r.passed for r in results):
        raise RuntimeError("Azure verification failed: " + "; ".join(r.message for r in results if not r.passed))
    return results
