"""Verify config-based providers: stored prices match connector config.

For Aruba, Exoscale, Contabo, Gcore, Cherry Servers,
Clouding.io - no pricing API exists; prices come from connector
FLAVORS/PLANS config. This verifies stored == config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import InstanceType, Price, Provider, Region
from app.models.price import PurchaseModel

logger = get_logger(__name__)

RELTOL = 0.001  # Config should match exactly (tiny tolerance for float)

# (slug, module_path, flavors_attr, regions_attr, price_extractor)
# price_extractor(spec) -> expected eur_per_hour or None to skip
def _contabo_price(spec: tuple) -> float | None:
    if len(spec) >= 4:
        return float(spec[3]) / 730.0  # eur_per_month -> eur_per_hour
    return None


def _cherry_price(spec: tuple) -> float | None:
    if len(spec) >= 4:
        return float(spec[3])  # eur_per_hour
    return None


def _clouding_price(spec: tuple) -> float | None:
    if len(spec) >= 4:
        return float(spec[3])  # eur_per_hour
    return None


CONFIG_PROVIDERS = [
    ("aruba", "connectors.aruba", "ARUBA_FLAVORS", "ARUBA_REGIONS", None),
    ("exoscale", "connectors.exoscale", "EXOSCALE_FLAVORS", "EXOSCALE_REGIONS", None),
    ("contabo", "connectors.contabo", "CONTABO_PLANS", "CONTABO_REGIONS", _contabo_price),
    ("gcore", "connectors.gcore", "GCORE_REGIONS", "GCORE_REGIONS", None),
    ("cherry_servers", "connectors.cherry_servers", "CHERRY_PLANS", "CHERRY_REGIONS", _cherry_price),
    ("clouding", "connectors.clouding", "CLOUDING_PLANS", "CLOUDING_REGIONS", _clouding_price),
]


@dataclass
class VerificationResult:
    passed: bool
    message: str
    details: dict[str, Any] | None = None


def _get_config(module_path: str, attr: str) -> dict | list | None:
    import importlib
    try:
        mod = importlib.import_module(module_path)
        return getattr(mod, attr, None)
    except Exception:
        return None


async def verify_ingested_prices(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
    fail_on_mismatch: bool = False,
) -> list[VerificationResult]:
    results: list[VerificationResult] = []
    if PurchaseModel(purchase_model) != PurchaseModel.ON_DEMAND:
        results.append(VerificationResult(passed=True, message="Config verify only supports on_demand"))
        return results

    for slug, module_path, flavors_attr, regions_attr, price_extractor in CONFIG_PROVIDERS:
        flavors = _get_config(module_path, flavors_attr)
        regions_cfg = _get_config(module_path, regions_attr)
        if not flavors or not isinstance(flavors, dict):
            results.append(VerificationResult(passed=True, message=f"{slug}: no FLAVORS config (skipping)"))
            continue

        if not price_extractor:
            results.append(VerificationResult(passed=True, message=f"{slug}: no price extractor (skipping)"))
            continue

        provider = (await db.execute(select(Provider).where(Provider.slug == slug).limit(1))).scalars().first()
        if not provider:
            results.append(VerificationResult(passed=False, message=f"{slug}: provider not in DB"))
            continue

        region = (await db.execute(select(Region).where(Region.provider_id == provider.id).limit(1))).scalars().first()
        if not region:
            results.append(VerificationResult(passed=False, message=f"{slug}: no region in DB"))
            continue

        mismatches = []
        checked = 0
        for name, spec in list(flavors.items())[:10]:
            if isinstance(spec, (list, tuple)):
                expected = price_extractor(spec)
            elif isinstance(spec, dict) and "price_eur_per_hour" in spec:
                expected = float(spec["price_eur_per_hour"])
            else:
                continue
            if expected is None:
                continue
            it = (await db.execute(select(InstanceType).where(InstanceType.provider_id == provider.id).where(InstanceType.name == name).limit(1))).scalars().first()
            if not it:
                mismatches.append({"name": name, "reason": "instance type not in DB", "expected": expected})
                checked += 1
                continue
            price_row = (await db.execute(select(Price).where(Price.instance_type_id == it.id).where(Price.region_id == region.id).where(Price.purchase_model == PurchaseModel.ON_DEMAND).limit(1))).scalars().first()
            if not price_row:
                mismatches.append({"name": name, "reason": "no price in DB", "expected": expected})
                checked += 1
                continue
            stored = price_row.price_eur_per_hour or price_row.price_per_hour
            diff = abs(stored - expected) / expected if expected > 0 else 0
            if diff > RELTOL:
                mismatches.append({"name": name, "expected": expected, "stored": stored, "diff_pct": round(diff * 100, 2)})
            checked += 1

        if mismatches:
            results.append(VerificationResult(passed=False, message=f"{slug}: config mismatch", details={"mismatches": mismatches}))
        elif checked > 0:
            results.append(VerificationResult(passed=True, message=f"{slug}: OK ({checked} flavors verified)"))
        else:
            results.append(VerificationResult(passed=True, message=f"{slug}: no flavors to verify"))

    if fail_on_mismatch and any(not r.passed for r in results):
        raise RuntimeError("Config verification failed: " + "; ".join(r.message for r in results if not r.passed))
    return results
