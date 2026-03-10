"""Verify GCP ingested prices match the Billing Catalog API.

Re-fetches prices from the GCP Billing Catalog API using the same logic as ingestion,
compares with stored DB values, and reports mismatches. Catches unit conversion errors
(e.g. per-minute stored as per-hour) and sanity-checks that machine prices are reasonable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur, to_eur
from app.core.logging import get_logger
from app.models import InstanceType, Price, Provider, Region
from app.models.price import PurchaseModel
from app.services.ingestion import pick_one_per_logical_product
from connectors.gcp import (
    GCP_CPU_PSEUDO_NAME,
    GCP_RAM_PSEUDO_NAME,
    _extract_unit_price_nanos,
    _get_billing_client,
    _is_base_on_demand_sku,
    _is_compute_linux_vm_sku,
    _is_preemptible_sku,
    _list_services,
    _list_skus,
    _service_regions,
)
from connectors.gcp_machine_types import GCP_MACHINE_TYPES, _get_latest_price

logger = get_logger(__name__)

# Relative tolerance for price comparison (FX rounding, floating point)
RELTOL = 0.005  # 0.5%

# Sanity check: e2-highcpu-2 in EU should be roughly 0.01-0.50 EUR/hour (Compute Engine)
# Values below this suggest per-minute stored as hourly or other unit error
MIN_REASONABLE_EUR_PER_HOUR = 0.01


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
    Verify ingested GCP prices match the Billing Catalog API.

    Returns list of VerificationResult. If fail_on_mismatch and any result failed,
    raises RuntimeError after collecting all results.
    """
    results: list[VerificationResult] = []
    purchase = PurchaseModel(purchase_model)

    # 1. Fetch expected prices from API (same logic as gcp.ingest)
    client = _get_billing_client()
    services = _list_services(client)
    compute_service = None
    for s in services:
        if s.display_name and "Compute Engine" in s.display_name:
            compute_service = s
            break
    if not compute_service:
        results.append(
            VerificationResult(passed=False, message="Compute Engine service not found in GCP Billing Catalog")
        )
        if fail_on_mismatch and any(not r.passed for r in results):
            raise RuntimeError("GCP verification failed: " + "; ".join(r.message for r in results if not r.passed))
        return results

    skus = _list_skus(client, compute_service.name)
    rate_usd_eur = await get_rate_to_eur(db, "USD")

    eu_region_prefixes = ("europe-", "eu-")

    def is_eu_region(region_id: str) -> bool:
        return any(region_id.lower().startswith(p) for p in eu_region_prefixes)

    # 2. Fetch provider, regions, instance types from DB
    provider = (await db.execute(select(Provider).where(Provider.slug == "gcp").limit(1))).scalars().first()
    if not provider:
        results.append(VerificationResult(passed=False, message="GCP provider not found in DB"))
        if fail_on_mismatch:
            raise RuntimeError("GCP verification failed: provider not found")
        return results

    regions_stmt = select(Region).where(Region.provider_id == provider.id)
    if only_eu:
        regions_stmt = regions_stmt.where(Region.is_eu.is_(True))
    regions = list((await db.execute(regions_stmt)).scalars().all())
    region_by_slug = {r.slug: r for r in regions}

    cpu_it = (
        await db.execute(
            select(InstanceType)
            .where(InstanceType.provider_id == provider.id)
            .where(InstanceType.name == GCP_CPU_PSEUDO_NAME)
            .limit(1)
        )
    ).scalars().first()
    ram_it = (
        await db.execute(
            select(InstanceType)
            .where(InstanceType.provider_id == provider.id)
            .where(InstanceType.name == GCP_RAM_PSEUDO_NAME)
            .limit(1)
        )
    ).scalars().first()

    if not cpu_it or not ram_it:
        results.append(VerificationResult(passed=False, message="GCP unit price instance types not found"))
        if fail_on_mismatch:
            raise RuntimeError("GCP verification failed: unit types not found")
        return results

    # 3. Build expected prices from API (same logic as gcp.ingest)
    candidates: list[dict] = []
    for sku in skus:
        if not _is_compute_linux_vm_sku(sku):
            continue
        if purchase == PurchaseModel.ON_DEMAND and _is_preemptible_sku(sku):
            continue
        if purchase == PurchaseModel.SPOT and not _is_preemptible_sku(sku):
            continue
        if purchase == PurchaseModel.ON_DEMAND and not _is_base_on_demand_sku(sku):
            continue
        price_info = _extract_unit_price_nanos(sku)
        if not price_info:
            continue
        currency, price_per_unit = price_info
        if price_per_unit <= 0:
            continue
        cat = sku.category
        rg = (cat.resource_group or "").upper() if cat else ""
        if "CPU" in rg:
            it = cpu_it
        elif "RAM" in rg:
            it = ram_it
        else:
            continue
        sku_name = getattr(sku, "name", "") or ""
        for reg in _service_regions(sku):
            if only_eu and not is_eu_region(reg):
                continue
            slug = reg.lower().replace("_", "-")
            if slug not in region_by_slug:
                continue
            candidates.append(
                {
                    "it": it,
                    "region_slug": slug,
                    "price_per_unit": price_per_unit,
                    "currency": currency,
                    "sku_name": sku_name,
                }
            )
    canonical = pick_one_per_logical_product(
        candidates,
        group_key=lambda c: (c["it"].name, c["region_slug"]),
        sort_key=lambda c: (c["sku_name"], c["price_per_unit"]),
    )
    expected: dict[tuple[str, str], float] = {}
    for c in canonical:
        price_eur = to_eur(c["price_per_unit"], rate_usd_eur) if c["currency"] != "EUR" else c["price_per_unit"]
        expected[(c["it"].name, c["region_slug"])] = price_eur

    # 4. Compare unit prices (CPU, RAM) per region
    mismatches: list[dict] = []
    for (pseudo, region_slug), expected_eur in expected.items():
        if region_slug not in region_by_slug:
            continue
        region = region_by_slug[region_slug]
        it = cpu_it if pseudo == GCP_CPU_PSEUDO_NAME else ram_it
        stored = await _get_latest_price(db, it.id, region.id, purchase)
        if not stored:
            mismatches.append(
                {
                    "type": pseudo,
                    "region": region_slug,
                    "expected_eur": expected_eur,
                    "stored_eur": None,
                    "reason": "no_price_in_db",
                }
            )
            continue
        stored_eur = stored.price_eur_per_hour
        if stored_eur is None:
            stored_eur = to_eur(stored.price_per_hour, rate_usd_eur)
        if stored_eur <= 0:
            rel_diff = 1.0
        else:
            rel_diff = abs(stored_eur - expected_eur) / stored_eur
        if rel_diff > RELTOL:
            mismatches.append(
                {
                    "type": pseudo,
                    "region": region_slug,
                    "expected_eur": expected_eur,
                    "stored_eur": stored_eur,
                    "rel_diff": rel_diff,
                    "reason": "mismatch",
                }
            )

    if mismatches:
        results.append(
            VerificationResult(
                passed=False,
                message=f"Unit price mismatch: {len(mismatches)} region(s) differ from API",
                details={"mismatches": mismatches[:10]},
            )
        )
        for m in mismatches[:5]:
            logger.warning(
                "GCP price mismatch: %s %s expected=%.6f stored=%.6f",
                m["type"],
                m["region"],
                m["expected_eur"],
                m.get("stored_eur") or 0,
            )
    else:
        results.append(VerificationResult(passed=True, message="Unit prices match API"))

    # 4. Sanity check: e2-highcpu-2 should be in reasonable range
    e2_highcpu_2 = next((m for m in GCP_MACHINE_TYPES if m.name == "e2-highcpu-2"), None)
    if e2_highcpu_2:
        low_prices: list[dict] = []
        for region in regions:
            machine_it = (
                await db.execute(
                    select(InstanceType)
                    .where(InstanceType.provider_id == provider.id)
                    .where(InstanceType.name == "e2-highcpu-2")
                    .limit(1)
                )
            ).scalars().first()
            if not machine_it:
                continue
            price = await _get_latest_price(db, machine_it.id, region.id, purchase)
            if not price:
                continue
            eur = price.price_eur_per_hour or to_eur(price.price_per_hour, rate_usd_eur)
            if eur < MIN_REASONABLE_EUR_PER_HOUR:
                low_prices.append({"region": region.slug, "eur_per_hour": eur})

        if low_prices:
            results.append(
                VerificationResult(
                    passed=False,
                    message=f"e2-highcpu-2 below {MIN_REASONABLE_EUR_PER_HOUR} EUR/h in {len(low_prices)} region(s) - likely unit error (e.g. per-minute as hourly)",
                    details={"low_prices": low_prices},
                )
            )
            for p in low_prices[:3]:
                logger.warning(
                    "GCP e2-highcpu-2 suspiciously low: %s %.6f EUR/h (expected ~0.02-0.10)",
                    p["region"],
                    p["eur_per_hour"],
                )
        else:
            results.append(VerificationResult(passed=True, message="e2-highcpu-2 prices in reasonable range"))

    # 5. Cross-check: recompute e2-highcpu-2 from unit prices and compare with stored
    e2_highcpu_2_spec = next((m for m in GCP_MACHINE_TYPES if m.name == "e2-highcpu-2"), None)
    if e2_highcpu_2_spec:
        recompute_mismatches: list[dict] = []
        for region in regions:
            cpu_price = await _get_latest_price(db, cpu_it.id, region.id, purchase)
            ram_price = await _get_latest_price(db, ram_it.id, region.id, purchase)
            if not cpu_price or not ram_price:
                continue
            cpu_eur = cpu_price.price_eur_per_hour or to_eur(cpu_price.price_per_hour, rate_usd_eur)
            ram_eur = ram_price.price_eur_per_hour or to_eur(ram_price.price_per_hour, rate_usd_eur)
            expected_machine_eur = (cpu_eur * e2_highcpu_2_spec.vcpu) + (ram_eur * e2_highcpu_2_spec.ram_gb)

            machine_it = (
                await db.execute(
                    select(InstanceType)
                    .where(InstanceType.provider_id == provider.id)
                    .where(InstanceType.name == "e2-highcpu-2")
                    .limit(1)
                )
            ).scalars().first()
            if not machine_it:
                continue
            stored_price = await _get_latest_price(db, machine_it.id, region.id, purchase)
            if not stored_price:
                continue
            stored_eur = stored_price.price_eur_per_hour or to_eur(stored_price.price_per_hour, rate_usd_eur)
            if stored_eur <= 0:
                rel_diff = 1.0
            else:
                rel_diff = abs(stored_eur - expected_machine_eur) / stored_eur
            if rel_diff > RELTOL:
                recompute_mismatches.append(
                    {
                        "region": region.slug,
                        "expected_eur": expected_machine_eur,
                        "stored_eur": stored_eur,
                        "rel_diff": rel_diff,
                    }
                )
        if recompute_mismatches:
            results.append(
                VerificationResult(
                    passed=False,
                    message=f"e2-highcpu-2 recomputed from unit prices differs in {len(recompute_mismatches)} region(s)",
                    details={"recompute_mismatches": recompute_mismatches[:5]},
                )
            )
        else:
            results.append(VerificationResult(passed=True, message="e2-highcpu-2 matches recomputation from unit prices"))

    if fail_on_mismatch and any(not r.passed for r in results):
        raise RuntimeError(
            "GCP verification failed: " + "; ".join(r.message for r in results if not r.passed)
        )

    return results
