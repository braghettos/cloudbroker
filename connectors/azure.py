"""Azure Retail Prices API connector: VM sizes and on-demand pricing.

Uses the public, unauthenticated Azure Retail Prices API (no Azure SDK required).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur, to_eur
from app.core.logging import get_logger
from app.models import Region
from app.models.price import PurchaseModel
from app.services.ingestion import (
    ensure_instance_type,
    ensure_provider,
    ensure_region,
    append_price,
)

logger = get_logger(__name__)

AZURE_RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"
AZURE_API_VERSION = "2023-01-01-preview"

# Azure VM size name pattern: Standard_<Series><Number>... e.g. Standard_D4s_v3, Standard_B2s
_AZURE_VM_SIZE_VCPU_RE = re.compile(r"Standard_[A-Za-z]+(\d+)[a-z_]*", re.IGNORECASE)


@dataclass
class AzureRegionSpec:
    """Azure region mapping: armRegionName -> our slug and metadata."""

    slug: str  # armRegionName (e.g. westeurope)
    name: str
    is_eu: bool


def parse_azure_vm_specs(product_name: str) -> tuple[int, float]:
    """Map Azure VM product name to (vcpu, ram_gb) for our schema.

    Azure naming: Standard_<Series><N>... e.g. Standard_D4s_v3 -> 4 vCPU.
    D/E/F series typically have 4 GB per vCPU; B-series (burstable) often 2 GB per vCPU.
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
    # B-series (burstable): often 2 GB per vCPU; others (D, E, F): 4 GB per vCPU
    if name.upper().startswith("STANDARD_B"):
        ram_gb = 2.0 * vcpu
    else:
        ram_gb = 4.0 * vcpu
    return vcpu, ram_gb


# Static list of Azure regions (subset for MVP; EU + a few US).
# armRegionName values match Azure Retail Prices API.
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
    """Fetch one page of Azure Retail Prices for Virtual Machines, Consumption, in a region.

    If next_page_link is provided, GET that URL; otherwise build initial $filter request.
    Returns (items, next_page_link or None).
    """
    if next_page_link:
        # NextPageLink can be full URL or path; ensure we use it as-is (API returns full URL)
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
    """Extract per-hour USD price from a Retail Prices item. Returns None if not hourly."""
    unit = (item.get("unitOfMeasure") or "").strip().lower()
    if "hour" not in unit and "hr" not in unit:
        return None
    try:
        return float(item.get("retailPrice", 0))
    except (TypeError, ValueError):
        return None


def _is_spot_vm_item(item: dict) -> bool:
    """Heuristically determine whether a Retail Prices item is for a Spot VM.

    Azure Retail Prices does not expose a dedicated 'spot' field, but spot meters
    are typically labeled with 'Spot' in skuName / productName / meterName.
    """
    text = " ".join(
        [
            str(item.get("skuName") or ""),
            str(item.get("productName") or ""),
            str(item.get("meterName") or ""),
        ]
    ).lower()
    return " spot" in text or "spot " in text or text.endswith("spot")


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
    region: str | None = None,
) -> None:
    """Ensure Azure provider and regions; ingest VM on-demand prices from Retail Prices API."""
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

    # Load region slug -> id for price writes
    regions_stmt = select(Region).where(Region.provider_id == provider.id)
    regions_result = await db.execute(regions_stmt)
    region_by_slug = {r.slug: r for r in regions_result.scalars().all()}

    # For now we support on-demand and spot via the same Retail Prices API.
    if purchase not in (PurchaseModel.ON_DEMAND, PurchaseModel.SPOT):
        logger.info(
            "Azure ingestion: only on_demand and spot implemented, skipping price ingestion",
            provider_slug="azure",
            purchase_model=purchase_model,
        )
        return

    rate_usd_eur = await get_rate_to_eur(db, "USD")
    inserted = 0
    instance_types_seen: set[str] = set()

    async with httpx.AsyncClient(timeout=60.0) as client:
        for spec in regions_to_use:
            reg = region_by_slug.get(spec.slug)
            if not reg:
                continue
            next_link: str | None = None
            while True:
                items, next_link = await _fetch_retail_prices_page(client, spec.slug, next_page_link=next_link)
                for item in items:
                    # API uses productName for category; VM size is in armSkuName or skuName.
                    sku = (item.get("armSkuName") or item.get("skuName") or "").strip()
                    if not sku or not sku.startswith("Standard_"):
                        continue

                    # Filter by purchase model: exclude Spot meters from on_demand,
                    # and include only Spot meters when purchase_model=spot.
                    is_spot = _is_spot_vm_item(item)
                    if purchase == PurchaseModel.ON_DEMAND and is_spot:
                        continue
                    if purchase == PurchaseModel.SPOT and not is_spot:
                        continue

                    price_usd = _extract_hourly_price_usd(item)
                    if price_usd is None or price_usd <= 0:
                        continue
                    vcpu, ram_gb = parse_azure_vm_specs(sku)
                    it = await ensure_instance_type(
                        db,
                        provider_id=provider.id,
                        name=sku,
                        vcpu=vcpu,
                        ram_gb=ram_gb,
                        family=_azure_family_from_product(sku),
                        generation=None,
                        arch="x86_64",
                        metadata_json=None,
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
                        price_per_hour=price_usd,
                        price_eur_per_hour=price_eur,
                    )
                    if p:
                        inserted += 1
                if not next_link:
                    break
                await asyncio.sleep(0.3)
            await asyncio.sleep(0.2)

    logger.info(
        "Azure ingestion completed",
        provider_slug="azure",
        regions=len(regions_to_use),
        instance_types=len(instance_types_seen),
        price_rows_inserted=inserted,
        only_eu=only_eu,
        purchase_model=purchase_model,
    )


def _azure_family_from_product(product_name: str) -> str | None:
    """Derive family from Azure product name (e.g. Standard_D4s_v3 -> D)."""
    if not product_name or not product_name.upper().startswith("STANDARD_"):
        return None
    parts = product_name.split("_")
    if len(parts) >= 2:
        return parts[1][:1].upper()
    return None
