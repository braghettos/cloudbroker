"""AWS Pricing API connector: EC2 instance types, on-demand and spot pricing.

Uses AWS Price List Query API (boto3 pricing client) for on-demand and
EC2 describe_spot_price_history for spot.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur, to_eur
from app.core.logging import get_logger
from app.models import InstanceType, Region
from app.models.price import PurchaseModel
from app.services.ingestion import ensure_instance_type, ensure_provider, ensure_region, append_price

logger = get_logger(__name__)

# Pricing API is only in us-east-1 and ap-south-1
AWS_PRICING_REGION = "us-east-1"
SERVICE_CODE_EC2 = "AmazonEC2"


@dataclass
class AwsRegionSpec:
    """AWS region mapping: API location name -> our slug and metadata."""

    location_name: str  # Value for Pricing API "location" filter
    slug: str  # Region code (e.g. eu-west-1)
    name: str
    is_eu: bool


# Static list of AWS regions (subset for MVP; expand as needed).
# Location names match AWS Pricing API GetProducts "location" attribute.
AWS_REGIONS: list[AwsRegionSpec] = [
    # EU
    AwsRegionSpec("EU (Ireland)", "eu-west-1", "Europe (Ireland)", True),
    AwsRegionSpec("EU (London)", "eu-west-2", "Europe (London)", True),
    AwsRegionSpec("EU (Paris)", "eu-west-3", "Europe (Paris)", True),
    AwsRegionSpec("EU (Frankfurt)", "eu-central-1", "Europe (Frankfurt)", True),
    AwsRegionSpec("EU (Stockholm)", "eu-north-1", "Europe (Stockholm)", True),
    AwsRegionSpec("EU (Milan)", "eu-south-1", "Europe (Milan)", True),
    AwsRegionSpec("Europe (Spain)", "eu-south-2", "Europe (Spain)", True),
    AwsRegionSpec("Europe (Zurich)", "eu-central-2", "Europe (Zurich)", True),
    # US
    AwsRegionSpec("US East (N. Virginia)", "us-east-1", "US East (N. Virginia)", False),
    AwsRegionSpec("US East (Ohio)", "us-east-2", "US East (Ohio)", False),
    AwsRegionSpec("US West (N. California)", "us-west-1", "US West (N. California)", False),
    AwsRegionSpec("US West (Oregon)", "us-west-2", "US West (Oregon)", False),
]


def _get_pricing_client():
    """Return boto3 Pricing client. Uses us-east-1 (Pricing API availability)."""
    try:
        import boto3
    except ImportError:
        raise RuntimeError("boto3 is required for AWS ingestion. Install with: pip install boto3")
    return boto3.client("pricing", region_name=AWS_PRICING_REGION)


def _get_ec2_client(region: str):
    """Return boto3 EC2 client for a given region (e.g. for Spot prices later)."""
    try:
        import boto3
    except ImportError:
        raise RuntimeError("boto3 is required for AWS ingestion. Install with: pip install boto3")
    return boto3.client("ec2", region_name=region)


def _parse_memory_gb(memory_str: str) -> float | None:
    """Parse AWS memory string like '4 GiB' or '8 GiB' to float GB."""
    if not memory_str:
        return None
    match = re.match(r"^([\d.]+)\s*GiB$", memory_str.strip(), re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def _parse_vcpu(vcpu_str: str) -> int:
    """Parse vCPU attribute to int (e.g. '2' -> 2)."""
    if not vcpu_str:
        return 0
    try:
        return int(float(vcpu_str))
    except (ValueError, TypeError):
        return 0


def _parse_gpu_count(gpu_str: str | None) -> int | None:
    """Parse AWS gpu attribute to int. Returns None if not present or zero."""
    if not gpu_str or gpu_str.strip().lower() in ("", "na", "n/a", "none", "0"):
        return None
    try:
        val = int(float(gpu_str.strip()))
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def _parse_gpu_memory_gb(gpu_memory_str: str | None) -> float | None:
    """Parse AWS gpuMemory string like '16 GiB' or '80 GiB' to float GB."""
    if not gpu_memory_str or gpu_memory_str.strip().lower() in ("", "na", "n/a", "none"):
        return None
    match = re.match(r"^([\d.]+)\s*GiB$", gpu_memory_str.strip(), re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def _parse_generation(current_generation: str | None) -> bool | None:
    """Parse AWS currentGeneration attribute ('Yes'/'No') to bool."""
    if not current_generation:
        return None
    return current_generation.strip().lower() == "yes"


def _normalize_network_performance(raw: str | None) -> str | None:
    """Truncate network performance string to 64 chars."""
    if not raw:
        return None
    return raw.strip()[:64] or None


def _map_aws_physical_processor_to_arch(physical_processor: str | None) -> str | None:
    """Map AWS physicalProcessor attribute to our arch (x86_64 or arm64) for recommendations.

    Recommendation API filters by arch in ('x86_64', 'arm64'). AWS returns values like
    'Intel Xeon Platinum 8175', 'AWS Graviton4 Processor', 'AMD EPYC 7R32'.
    """
    if not physical_processor or not physical_processor.strip():
        return None
    raw = physical_processor.strip().lower()
    if "graviton" in raw or "arm" in raw:
        return "arm64"
    if "intel" in raw or "amd" in raw or "xeon" in raw or "epyc" in raw:
        return "x86_64"
    # Default unknown to x86_64 so instances still appear in x86_64 recommendations
    return "x86_64"


def _extract_ondemand_price_usd(product: dict) -> float | None:
    """Extract hourly USD price from GetProducts item. Returns None if not OnDemand or no price."""
    try:
        terms = product.get("terms") or {}
        on_demand = terms.get("OnDemand")
        if not on_demand:
            return None
        # First (and usually only) SKU key
        for sku_data in on_demand.values():
            dims = sku_data.get("priceDimensions") or {}
            for dim in dims.values():
                unit = (dim.get("unit") or "").strip().lower()
                if unit != "hrs":
                    continue
                price_per = dim.get("pricePerUnit") or {}
                usd = price_per.get("USD")
                if usd is not None:
                    return float(usd)
            break
    except (TypeError, ValueError, KeyError):
        pass
    return None


def _fetch_ec2_ondemand_for_location(
    pricing_client: Any,
    location_name: str,
    rate_limit_delay_sec: float = 0.25,
) -> list[tuple[dict[str, str], float]]:
    """Call GetProducts for one location; return list of (attributes dict, USD per hour). Sync."""
    filters = [
        {"Type": "TERM_MATCH", "Field": "location", "Value": location_name},
        {"Type": "TERM_MATCH", "Field": "termType", "Value": "OnDemand"},
        {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
        {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
        {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Compute Instance"},
        # Exclude pre-installed software (SQL Web, SQL Std, etc.) — base Linux price only
        {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
        # Exclude capacity reservation SKUs; only regular on-demand usage
        {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
    ]
    results: list[tuple[dict[str, str], float]] = []
    next_token = None
    while True:
        kwargs = {
            "ServiceCode": SERVICE_CODE_EC2,
            "Filters": filters,
            "MaxResults": 100,
        }
        if next_token:
            kwargs["NextToken"] = next_token
        resp = pricing_client.get_products(**kwargs)
        time.sleep(rate_limit_delay_sec)
        for item in resp.get("PriceList") or []:
            try:
                data = json.loads(item) if isinstance(item, str) else item
                product = data.get("product") or {}
                attrs = product.get("attributes") or {}
                if product.get("productFamily") != "Compute Instance":
                    continue
                price_usd = _extract_ondemand_price_usd(data)
                if price_usd is None or price_usd <= 0:
                    continue
                instance_type = (attrs.get("instanceType") or "").strip()
                if not instance_type:
                    continue
                results.append((attrs, price_usd))
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
        next_token = resp.get("NextToken")
        if not next_token:
            break
    return results


# Chunk size for EC2 describe_spot_price_history (instance types per request)
SPOT_INSTANCE_TYPES_CHUNK = 50


def _fetch_spot_prices_for_region(
    ec2_client: Any,
    instance_type_names: list[str],
    rate_limit_delay_sec: float = 0.2,
) -> dict[str, float]:
    """Call describe_spot_price_history for a region; return instance_type -> USD per hour. Sync."""
    if not instance_type_names:
        return {}
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=1)
    result: dict[str, float] = {}
    for i in range(0, len(instance_type_names), SPOT_INSTANCE_TYPES_CHUNK):
        chunk = instance_type_names[i : i + SPOT_INSTANCE_TYPES_CHUNK]
        try:
            resp = ec2_client.describe_spot_price_history(
                StartTime=start,
                EndTime=end,
                InstanceTypes=chunk,
                ProductDescriptions=["Linux/UNIX"],
            )
        except Exception:
            time.sleep(rate_limit_delay_sec)
            continue
        time.sleep(rate_limit_delay_sec)
        for item in resp.get("SpotPriceHistory") or []:
            name = (item.get("InstanceType") or "").strip()
            if not name:
                continue
            try:
                price = float(item.get("SpotPrice", 0))
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            # Keep minimum price per instance type (best for user)
            if name not in result or price < result[name]:
                result[name] = price
    return result


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
    region: str | None = None,
) -> None:
    """Ensure AWS provider and regions; ingest EC2 on-demand instance types and prices."""
    from app.models.provider import ProviderType

    purchase = PurchaseModel(purchase_model)
    regions_to_use = [r for r in AWS_REGIONS if not only_eu or r.is_eu]
    if region:
        regions_to_use = [r for r in regions_to_use if r.slug == region]
        if not regions_to_use:
            logger.warning(
                "AWS ingestion: region not in catalog, ignoring",
                region=region,
                only_eu=only_eu,
            )
            return

    # Ensure AWS provider
    provider = await ensure_provider(
        db,
        slug="aws",
        name="Amazon Web Services",
        website="https://aws.amazon.com",
        type=ProviderType.HYPERSCALER,
    )
    await db.flush()

    # Ensure regions from static mapping
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

    if purchase == PurchaseModel.ON_DEMAND:
        await _run_ondemand_ingest(db, provider, regions_to_use, region_by_slug, only_eu)
        return
    if purchase == PurchaseModel.SPOT:
        await _run_spot_ingest(db, provider, regions_to_use, region_by_slug, only_eu)
        return
    logger.info(
        "AWS ingestion: purchase model not implemented, skipping price ingestion",
        provider_slug="aws",
        purchase_model=purchase_model,
    )


async def _run_ondemand_ingest(
    db: AsyncSession,
    provider: Any,
    regions_to_use: list,
    region_by_slug: dict,
    only_eu: bool,
) -> None:
    rate_usd_eur = await get_rate_to_eur(db, "USD")
    pricing_client = _get_pricing_client()
    inserted = 0
    instance_types_seen: set[str] = set()

    for spec in regions_to_use:
        region = region_by_slug.get(spec.slug)
        if not region:
            continue
        loop = asyncio.get_event_loop()
        items = await loop.run_in_executor(
            None,
            lambda loc=spec.location_name: _fetch_ec2_ondemand_for_location(pricing_client, loc),
        )
        for attrs, price_usd in items:
            name = (attrs.get("instanceType") or "").strip()
            if not name:
                continue
            vcpu = _parse_vcpu(attrs.get("vcpu") or "")
            memory_str = attrs.get("memory") or ""
            ram_gb = _parse_memory_gb(memory_str)
            if ram_gb is None or ram_gb <= 0:
                ram_gb = 0.0
            family = (attrs.get("instanceFamily") or "").strip() or None
            if family and len(family) > 64:
                family = family[:64]
            arch = _map_aws_physical_processor_to_arch(attrs.get("physicalProcessor"))
            is_current_gen = _parse_generation(attrs.get("currentGeneration"))
            gpu_count = _parse_gpu_count(attrs.get("gpu"))
            gpu_memory_gb = _parse_gpu_memory_gb(attrs.get("gpuMemory"))
            gpu_model = (attrs.get("elasticGpuType") or "").strip() or None
            network_performance = _normalize_network_performance(attrs.get("networkPerformance"))
            metadata = (
                json.dumps(
                    {
                        "instanceFamily": attrs.get("instanceFamily"),
                        "networkPerformance": attrs.get("networkPerformance"),
                        "storage": attrs.get("storage"),
                        "physicalProcessor": attrs.get("physicalProcessor"),
                    }
                )
                if any(attrs.get(k) for k in ("instanceFamily", "networkPerformance", "storage", "physicalProcessor"))
                else None
            )

            it = await ensure_instance_type(
                db,
                provider_id=provider.id,
                name=name,
                vcpu=vcpu,
                ram_gb=ram_gb,
                family=family,
                arch=arch,
                is_current_gen=is_current_gen,
                gpu_count=gpu_count,
                gpu_model=gpu_model,
                gpu_memory_gb=gpu_memory_gb,
                network_performance=network_performance,
                metadata_json=metadata,
            )
            await db.flush()
            instance_types_seen.add(name)

            price_eur = to_eur(price_usd, rate_usd_eur)
            p = await append_price(
                db,
                instance_type_id=it.id,
                region_id=region.id,
                purchase_model=PurchaseModel.ON_DEMAND,
                currency="USD",
                price_per_hour=price_usd,
                price_eur_per_hour=price_eur,
            )
            if p:
                inserted += 1
        await asyncio.sleep(0.3)

    logger.info(
        "AWS ingestion (on-demand) completed",
        provider_slug="aws",
        regions=len(regions_to_use),
        instance_types=len(instance_types_seen),
        price_rows_inserted=inserted,
        only_eu=only_eu,
    )


async def _run_spot_ingest(
    db: AsyncSession,
    provider: Any,
    regions_to_use: list,
    region_by_slug: dict,
    only_eu: bool,
) -> None:
    """Ingest EC2 spot prices; requires instance types to exist (run on-demand first)."""
    stmt = select(InstanceType).where(InstanceType.provider_id == provider.id)
    result = await db.execute(stmt)
    instance_types = list(result.scalars().all())
    if not instance_types:
        logger.warning(
            "AWS spot ingestion: no instance types found; run on-demand ingestion first",
            provider_slug="aws",
        )
        return

    name_to_it = {it.name: it for it in instance_types}
    instance_type_names = list(name_to_it.keys())
    rate_usd_eur = await get_rate_to_eur(db, "USD")
    loop = asyncio.get_event_loop()
    inserted = 0

    for spec in regions_to_use:
        region = region_by_slug.get(spec.slug)
        if not region:
            continue
        ec2_client = _get_ec2_client(spec.slug)
        prices_map = await loop.run_in_executor(
            None,
            lambda: _fetch_spot_prices_for_region(ec2_client, instance_type_names),
        )
        for name, price_usd in prices_map.items():
            it = name_to_it.get(name)
            if not it:
                continue
            price_eur = to_eur(price_usd, rate_usd_eur)
            p = await append_price(
                db,
                instance_type_id=it.id,
                region_id=region.id,
                purchase_model=PurchaseModel.SPOT,
                currency="USD",
                price_per_hour=price_usd,
                price_eur_per_hour=price_eur,
            )
            if p:
                inserted += 1
        await asyncio.sleep(0.25)

    logger.info(
        "AWS ingestion (spot) completed",
        provider_slug="aws",
        regions=len(regions_to_use),
        instance_types=len(instance_type_names),
        price_rows_inserted=inserted,
        only_eu=only_eu,
    )
