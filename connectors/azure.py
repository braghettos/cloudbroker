"""Azure connector: VM sizes and on-demand pricing.

Pricing source: Azure Retail Prices API (public, no auth required).
Instance specs source: Azure Resource SKUs API (requires Azure SDK + auth).

Falls back to heuristic RAM/vCPU derivation when the Resource SKUs API is
unavailable (e.g. no Azure credentials configured).
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur, to_eur
from app.core.logging import get_logger
from app.models import Region
from app.models.price import PurchaseModel
from app.services.ingestion import (
    append_price,
    ensure_instance_type,
    ensure_provider,
    ensure_region,
    pick_one_per_logical_product,
)

logger = get_logger(__name__)

AZURE_RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"
AZURE_API_VERSION = "2023-01-01-preview"

# Azure Resource SKUs API endpoint template (requires Azure subscription ID)
AZURE_SKU_API_URL = "https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.Compute/skus"
AZURE_SKU_API_VERSION = "2021-07-01"

# Fallback regex: Standard_D4s_v3 -> vcpu=4
_AZURE_VM_SIZE_VCPU_RE = re.compile(r"Standard_[A-Za-z]+(\d+)[a-z_]*", re.IGNORECASE)

# Azure VM generation pattern: _v3, _v4, _v5 suffix
_AZURE_VM_GEN_RE = re.compile(r"_v(\d+)$", re.IGNORECASE)

# GPU series: N-series VMs have GPUs
_AZURE_GPU_SERIES = {"NC", "ND", "NV", "NVv", "NCv", "NDv", "NVas", "NCas", "NDas"}

# ARM series: Dpsv5, Epsv5, Dpdsv5, Dpdsv6, etc.
_AZURE_ARM_PATTERNS = re.compile(r"Standard_[DE]p[a-z]*[sv]\d+", re.IGNORECASE)

# Burstable (shared CPU) series
_AZURE_BURSTABLE_SERIES = {"B", "Bs", "Bms"}

# Family normalization to common taxonomy
_AZURE_FAMILY_NORMALIZE: dict[str, str] = {
    "A": "general-purpose",
    "B": "general-purpose",
    "D": "general-purpose",
    "E": "memory-optimized",
    "F": "compute-optimized",
    "G": "memory-optimized",
    "H": "compute-optimized",
    "L": "storage-optimized",
    "M": "memory-optimized",
    "N": "gpu",
}

# GPU model lookup by Azure series
_AZURE_GPU_LOOKUP: dict[str, tuple[str, float]] = {
    "NC6s_v3": ("nvidia-v100", 16.0),
    "NC12s_v3": ("nvidia-v100", 32.0),
    "NC24s_v3": ("nvidia-v100", 64.0),
    "NC4as_T4_v3": ("nvidia-t4", 16.0),
    "NC8as_T4_v3": ("nvidia-t4", 16.0),
    "NC16as_T4_v3": ("nvidia-t4", 16.0),
    "NC64as_T4_v3": ("nvidia-t4", 64.0),
    "NC24ads_A100_v4": ("nvidia-a100", 80.0),
    "NC48ads_A100_v4": ("nvidia-a100", 160.0),
    "NC96ads_A100_v4": ("nvidia-a100", 320.0),
    "ND96asr_v4": ("nvidia-a100", 320.0),
    "ND96amsr_A100_v4": ("nvidia-a100", 320.0),
    "NV6": ("nvidia-m60", 8.0),
    "NV12": ("nvidia-m60", 16.0),
    "NV24": ("nvidia-m60", 24.0),
    "NV12s_v3": ("nvidia-m60", 8.0),
    "NV24s_v3": ("nvidia-m60", 16.0),
    "NV48s_v3": ("nvidia-m60", 24.0),
}


@dataclass
class AzureRegionSpec:
    """Azure region mapping: armRegionName -> our slug and metadata."""

    slug: str  # armRegionName (e.g. westeurope)
    name: str
    is_eu: bool


@dataclass
class AzureSkuSpec:
    """Normalized VM spec from Resource SKUs API."""

    name: str
    vcpu: int
    ram_gb: float
    gpu_count: int | None = None
    gpu_model: str | None = None
    generation: str | None = None
    is_current_gen: bool | None = None


def _parse_sku_capabilities(capabilities: list[dict]) -> dict[str, str]:
    """Convert SKU capabilities list to a name -> value dict."""
    return {c.get("name", ""): c.get("value", "") for c in capabilities if c.get("name")}


def _extract_sku_specs(sku: dict) -> AzureSkuSpec | None:
    """Extract VM specs from a Resource SKUs API item."""
    name = sku.get("name", "").strip()
    if not name:
        return None
    caps = _parse_sku_capabilities(sku.get("capabilities") or [])
    try:
        vcpu_str = caps.get("vCPUs") or caps.get("vCPUsAvailable") or "0"
        vcpu = int(float(vcpu_str))
        if vcpu <= 0:
            return None
    except (ValueError, TypeError):
        return None
    try:
        ram_str = caps.get("MemoryGB") or "0"
        ram_gb = float(ram_str)
        if ram_gb <= 0:
            return None
    except (ValueError, TypeError):
        return None

    gpu_count: int | None = None
    gpu_model: str | None = None
    try:
        gpu_str = caps.get("GPUs") or "0"
        gpu_val = int(float(gpu_str))
        if gpu_val > 0:
            gpu_count = gpu_val
    except (ValueError, TypeError):
        pass

    generation: str | None = None
    gen_match = _AZURE_VM_GEN_RE.search(name)
    if gen_match:
        generation = f"v{gen_match.group(1)}"

    return AzureSkuSpec(
        name=name,
        vcpu=vcpu,
        ram_gb=ram_gb,
        gpu_count=gpu_count,
        gpu_model=gpu_model,
        generation=generation,
    )


def _fetch_resource_skus_sync(subscription_id: str, access_token: str) -> dict[str, AzureSkuSpec]:
    """Fetch VM SKU specs from Azure Resource SKUs API. Returns name -> AzureSkuSpec. Sync."""
    import urllib.request
    import json as _json

    url = f"{AZURE_SKU_API_URL.format(subscription_id=subscription_id)}?api-version={AZURE_SKU_API_VERSION}&$filter=resourceType eq 'virtualMachines'"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    specs: dict[str, AzureSkuSpec] = {}
    while url:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = _json.loads(resp.read())
        except Exception as e:
            logger.warning("Azure Resource SKUs API fetch failed", error=str(e))
            break
        for item in data.get("value") or []:
            if item.get("resourceType") != "virtualMachines":
                continue
            spec = _extract_sku_specs(item)
            if spec and spec.name not in specs:
                specs[spec.name] = spec
        url = data.get("nextLink")
    return specs


async def _load_sku_specs() -> dict[str, AzureSkuSpec]:
    """Load VM specs from Azure Resource SKUs API if credentials are available.

    Requires AZURE_SUBSCRIPTION_ID and AZURE_ACCESS_TOKEN (or falls back via
    azure-identity DefaultAzureCredential if the azure-identity package is installed).

    Returns empty dict if credentials are not available, causing the connector to fall
    back to heuristic spec derivation.
    """
    from app.core.config import settings as app_settings
    subscription_id = app_settings.azure_subscription_id or ""
    access_token = app_settings.azure_access_token or ""

    if not subscription_id:
        logger.info("Azure Resource SKUs API skipped: AZURE_SUBSCRIPTION_ID not set; using heuristic specs")
        return {}

    if not access_token:
        # Try azure-identity DefaultAzureCredential
        try:
            from azure.identity import DefaultAzureCredential  # type: ignore[import]

            cred = DefaultAzureCredential()
            token = cred.get_token("https://management.azure.com/.default")
            access_token = token.token
        except Exception as e:
            logger.info(
                "Azure Resource SKUs API skipped: no AZURE_ACCESS_TOKEN and azure-identity unavailable",
                error=str(e),
            )
            return {}

    loop = asyncio.get_event_loop()
    specs = await loop.run_in_executor(None, lambda: _fetch_resource_skus_sync(subscription_id, access_token))
    logger.info("Azure Resource SKUs API loaded", sku_count=len(specs))
    return specs


def parse_azure_vm_specs_heuristic(product_name: str) -> tuple[int, float]:
    """Heuristic fallback: derive (vcpu, ram_gb) from Azure VM SKU name.

    Only used when Azure Resource SKUs API is not available. Results are approximate:
    - B-series (burstable): 2 GB/vCPU
    - All others: 4 GB/vCPU

    For accurate specs use the Resource SKUs API via _load_sku_specs().
    """
    if not product_name or not product_name.strip():
        return 1, 1.0
    name = product_name.strip()
    match = _AZURE_VM_SIZE_VCPU_RE.match(name)
    if not match:
        return 1, 1.0
    vcpu = int(match.group(1))
    if vcpu <= 0:
        vcpu = 1
    if name.upper().startswith("STANDARD_B"):
        ram_gb = 2.0 * vcpu
    else:
        ram_gb = 4.0 * vcpu
    return vcpu, ram_gb


# Static list of Azure regions (subset for MVP; EU + a few US).
AZURE_REGIONS: list[AzureRegionSpec] = [
    # EU
    AzureRegionSpec("westeurope", "West Europe", True),
    AzureRegionSpec("northeurope", "North Europe", True),
    AzureRegionSpec("uksouth", "UK South", True),
    AzureRegionSpec("francecentral", "France Central", True),
    AzureRegionSpec("germanywestcentral", "Germany West Central", True),
    AzureRegionSpec("swedencentral", "Sweden Central", True),
    AzureRegionSpec("switzerlandnorth", "Switzerland North", True),
    # US
    AzureRegionSpec("eastus", "East US", False),
    AzureRegionSpec("eastus2", "East US 2", False),
    AzureRegionSpec("westus2", "West US 2", False),
]


async def _fetch_retail_prices_page(
    client: httpx.AsyncClient,
    arm_region_name: str,
    next_page_link: str | None = None,
) -> tuple[list[dict], str | None]:
    """Fetch one page of Azure Retail Prices for Virtual Machines, Consumption, in a region."""
    if next_page_link:
        url = (
            next_page_link
            if next_page_link.startswith("http")
            else f"{AZURE_RETAIL_PRICES_URL.rstrip('/')}{next_page_link if next_page_link.startswith('/') else '/' + next_page_link}"
        )
        r = await client.get(url, timeout=60.0)
    else:
        params = {
            "api-version": AZURE_API_VERSION,
            "$filter": (
                f"serviceName eq 'Virtual Machines' and armRegionName eq '{arm_region_name}' and type eq 'Consumption'"
            ),
        }
        r = await client.get(AZURE_RETAIL_PRICES_URL, params=params, timeout=60.0)
    r.raise_for_status()
    data = r.json()
    items = data.get("Items") or []
    next_link = data.get("NextPageLink")
    return items, next_link


def _extract_hourly_price_usd(item: dict) -> float | None:
    """Extract per-hour USD price from a Retail Prices item. Returns None if not hourly or zero."""
    unit = (item.get("unitOfMeasure") or "").strip().lower()
    if "hour" not in unit and "hr" not in unit:
        return None
    try:
        price = float(item.get("retailPrice") or item.get("unitPrice") or 0)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _is_linux_product(item: dict) -> bool:
    """Prefer Linux over Windows for consistent single price per logical product."""
    name = (item.get("productName") or "").lower()
    return "linux" in name or "linux" in (item.get("meterName") or "").lower()


def _is_spot_vm_item(item: dict) -> bool:
    """Heuristically determine whether a Retail Prices item is for a Spot VM."""
    text = " ".join(
        [
            str(item.get("skuName") or ""),
            str(item.get("productName") or ""),
            str(item.get("meterName") or ""),
        ]
    ).lower()
    return " spot" in text or "spot " in text or text.endswith("spot")


def _parse_effective_date(item: dict) -> datetime | None:
    """Parse effectiveStartDate from Retail Prices item."""
    raw = item.get("effectiveStartDate") or ""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _azure_family_from_product(product_name: str) -> str | None:
    """Derive normalized family from Azure product name (e.g. Standard_D4s_v3 -> general-purpose)."""
    if not product_name or not product_name.upper().startswith("STANDARD_"):
        return None
    parts = product_name.split("_")
    if len(parts) >= 2:
        letter = parts[1][:1].upper()
        return _AZURE_FAMILY_NORMALIZE.get(letter, "general-purpose")
    return None


def _azure_detect_arch(sku_name: str) -> str:
    """Detect architecture from Azure SKU name. ARM instances use Dp/Ep prefixes."""
    if _AZURE_ARM_PATTERNS.match(sku_name):
        return "arm64"
    return "x86_64"


def _azure_infer_cpu_type(sku_name: str) -> str | None:
    """Infer cpu_type from Azure SKU name. B-series is burstable/shared."""
    if not sku_name or not sku_name.upper().startswith("STANDARD_"):
        return None
    parts = sku_name.split("_")
    if len(parts) >= 2:
        # Extract series letter(s): "B2s" -> "B", "Bs" -> "B", "Bms" -> "B"
        series_part = parts[1]
        first_letter = series_part[:1].upper()
        if first_letter == "B":
            return "shared"
    return "dedicated"


def _azure_is_current_gen(sku_name: str) -> bool | None:
    """Heuristic: v4+ and v5+ are current generation."""
    gen_match = _AZURE_VM_GEN_RE.search(sku_name)
    if gen_match:
        gen_num = int(gen_match.group(1))
        return gen_num >= 4
    return None


def _azure_enrich_gpu(sku_name: str, gpu_count: int | None) -> tuple[int | None, str | None, float | None]:
    """Enrich GPU model and memory from Azure SKU lookup table."""
    if not sku_name or not sku_name.upper().startswith("STANDARD_"):
        return gpu_count, None, None
    # Strip "Standard_" prefix for lookup
    bare = sku_name.replace("Standard_", "")
    if bare in _AZURE_GPU_LOOKUP:
        model, mem = _AZURE_GPU_LOOKUP[bare]
        return gpu_count or 1, model, mem
    return gpu_count, None, None


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
    region: str | None = None,
) -> None:
    """Ensure Azure provider and regions; ingest VM on-demand prices from Retail Prices API.

    VM specs (vcpu, ram_gb, gpu_count, generation) are fetched from Azure Resource SKUs API
    when AZURE_SUBSCRIPTION_ID is set, otherwise falls back to heuristic derivation.
    """
    from app.models.provider import ProviderType

    purchase = PurchaseModel(purchase_model)
    regions_to_use = [r for r in AZURE_REGIONS if not only_eu or r.is_eu]
    if region:
        regions_to_use = [r for r in regions_to_use if r.slug == region]
        if not regions_to_use:
            logger.warning(
                "Azure ingestion: region not in catalog, ignoring",
                region=region,
                only_eu=only_eu,
            )
            return

    provider = await ensure_provider(
        db,
        slug="azure",
        name="Microsoft Azure",
        website="https://azure.microsoft.com",
        type=ProviderType.HYPERSCALER,
    )
    await db.flush()

    for spec in regions_to_use:
        await ensure_region(
            db,
            provider_id=provider.id,
            slug=spec.slug,
            name=spec.name,
            country=None,
            is_eu=spec.is_eu,
        )
    await db.flush()

    regions_stmt = select(Region).where(Region.provider_id == provider.id)
    regions_result = await db.execute(regions_stmt)
    region_by_slug = {r.slug: r for r in regions_result.scalars().all()}

    if purchase not in (PurchaseModel.ON_DEMAND, PurchaseModel.SPOT):
        logger.info(
            "Azure ingestion: only on_demand and spot implemented, skipping price ingestion",
            provider_slug="azure",
            purchase_model=purchase_model,
        )
        return

    # Load accurate specs from Resource SKUs API (empty dict = use heuristic fallback)
    sku_specs = await _load_sku_specs()
    using_accurate_specs = bool(sku_specs)

    rate_usd_eur = await get_rate_to_eur(db, "USD")
    inserted = 0
    instance_types_seen: set[str] = set()

    async with httpx.AsyncClient(timeout=60.0) as client:
        for spec in regions_to_use:
            reg = region_by_slug.get(spec.slug)
            if not reg:
                continue
            next_link: str | None = None
            all_candidates: list[dict] = []
            while True:
                items, next_link = await _fetch_retail_prices_page(client, spec.slug, next_page_link=next_link)
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
                await asyncio.sleep(0.3)

            canonical = pick_one_per_logical_product(
                all_candidates,
                group_key=lambda i: (
                    (i.get("armSkuName") or i.get("skuName") or "").strip(),
                    i.get("armRegionName", ""),
                ),
                sort_key=lambda i: (0 if _is_linux_product(i) else 1, i.get("skuId", ""), i.get("meterId", "")),
            )
            for item in canonical:
                sku = (item.get("armSkuName") or item.get("skuName") or "").strip()
                price_usd = _extract_hourly_price_usd(item)
                if price_usd is None or price_usd <= 0:
                    continue
                effective_date = _parse_effective_date(item)

                # Use accurate specs from Resource SKUs API when available
                sku_spec = sku_specs.get(sku) if using_accurate_specs else None
                if sku_spec:
                    vcpu = sku_spec.vcpu
                    ram_gb = sku_spec.ram_gb
                    gpu_count = sku_spec.gpu_count
                    gpu_model = sku_spec.gpu_model
                    generation = sku_spec.generation
                else:
                    vcpu, ram_gb = parse_azure_vm_specs_heuristic(sku)
                    gpu_count = None
                    gpu_model = None
                    generation = None
                    gen_match = _AZURE_VM_GEN_RE.search(sku)
                    if gen_match:
                        generation = f"v{gen_match.group(1)}"

                # Enrich GPU metadata from lookup table
                gpu_count, gpu_model, gpu_memory_gb = _azure_enrich_gpu(sku, gpu_count)

                it = await ensure_instance_type(
                    db,
                    provider_id=provider.id,
                    name=sku,
                    vcpu=vcpu,
                    ram_gb=ram_gb,
                    family=_azure_family_from_product(sku),
                    generation=generation,
                    arch=_azure_detect_arch(sku),
                    cpu_type=_azure_infer_cpu_type(sku),
                    is_current_gen=_azure_is_current_gen(sku),
                    gpu_count=gpu_count,
                    gpu_model=gpu_model,
                    gpu_memory_gb=gpu_memory_gb,
                )
                await db.flush()
                instance_types_seen.add(sku)

                price_eur = to_eur(price_usd, rate_usd_eur)
                p = await append_price(
                    db,
                    instance_type_id=it.id,
                    region_id=reg.id,
                    purchase_model=purchase,
                    currency="USD",
                    source_currency="USD",
                    price_per_hour=price_usd,
                    price_eur_per_hour=price_eur,
                    effective_date=effective_date,
                )
                if p:
                    inserted += 1
            await asyncio.sleep(0.2)

    logger.info(
        "Azure ingestion completed",
        provider_slug="azure",
        regions=len(regions_to_use),
        instance_types=len(instance_types_seen),
        price_rows_inserted=inserted,
        only_eu=only_eu,
        purchase_model=purchase_model,
        used_resource_skus_api=using_accurate_specs,
    )
