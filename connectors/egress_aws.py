"""AWS egress rate connector: fetch Data Transfer Out pricing from Pricing API."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

# AWS Pricing API: data transfer is under AmazonEC2 with usageType filter
# EU (Ireland) eu-west-1 as representative region
_REP_LOCATION = "EU (Ireland)"


from connectors._cloud_clients import get_aws_pricing_client


def _fetch_data_transfer_products_sync() -> list[dict]:
    """Fetch GetProducts for EC2 data transfer. Returns list of product dicts."""
    client = get_aws_pricing_client()
    products: list[dict] = []
    next_token = None
    # productFamily "Data Transfer" + location to get region-specific egress
    filters = [
        {"Type": "TERM_MATCH", "Field": "location", "Value": _REP_LOCATION},
        {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Data Transfer"},
    ]
    while True:
        kw: dict[str, Any] = {
            "ServiceCode": "AmazonEC2",
            "Filters": filters,
            "MaxResults": 100,
        }
        if next_token:
            kw["NextToken"] = next_token
        resp = client.get_products(**kw)
        for item in resp.get("PriceList", []):
            try:
                products.append(json.loads(item))
            except (json.JSONDecodeError, TypeError):
                continue
        next_token = resp.get("NextToken")
        if not next_token:
            break
    return products


def _extract_egress_usd_from_product(product: dict) -> tuple[float, float] | None:
    """
    Extract (usd_per_gb, free_tier_gb) from AWS GetProducts item.
    AWS data transfer uses terms.OnDemand with priceDimensions; unit is often 'GB'.
    """
    try:
        terms = product.get("terms") or {}
        on_demand = terms.get("OnDemand")
        if not on_demand:
            return None
        attrs = product.get("attributes") or {}
        usage_type = (attrs.get("usagetype") or "").lower()
        # We want internet egress, not inter-region or in
        if "in" in usage_type and "out" not in usage_type:
            return None
        if "regional" in usage_type or "inter" in usage_type:
            return None
        # DataTransfer-Out-Bytes or similar
        for sku_data in on_demand.values():
            dims = sku_data.get("priceDimensions") or {}
            for dim in dims.values():
                unit = (dim.get("unit") or "").strip().lower()
                if "gb" not in unit and "byte" not in unit:
                    continue
                price_per = dim.get("pricePerUnit") or {}
                usd = price_per.get("USD")
                if usd is not None:
                    price = float(usd)
                    # AWS first 100 GB free in some regions; simplify to 0 for now
                    free_gb = 100.0 if "free" in (dim.get("description") or "").lower() else 0.0
                    return (price, free_gb)
        return None
    except (TypeError, ValueError, KeyError):
        return None


def _fetch_egress_rates_aws() -> tuple[float, float, float | None]:
    """
    Fetch AWS internet egress rate. Returns (egress_usd_per_gb, free_gb, cross_region_usd).
    Falls back to reference values if API returns nothing.
    """
    products = _fetch_data_transfer_products_sync()
    # Filter for outbound to internet (not inter-region, not in)
    for p in products:
        attrs = p.get("attributes") or {}
        usage = (attrs.get("usagetype") or "").lower()
        if "datatransfer" not in usage and "data-transfer" not in usage:
            continue
        if "in" in usage or "inbound" in usage:
            continue
        if "region" in usage or "inter" in usage:
            continue
        extracted = _extract_egress_usd_from_product(p)
        if extracted:
            usd_per_gb, free_gb = extracted
            # Cross-region: typically ~0.02 USD/GB
            cross_usd = 0.02
            return (usd_per_gb, free_gb, cross_usd)
    logger.warning("AWS egress: no matching Data Transfer products found")
    return (0.10, 0.0, 0.02)


async def ingest_egress_aws(db: AsyncSession) -> None:
    """Ingest AWS egress rates and upsert into egress_rates."""
    from connectors._egress_helper import ingest_egress_from_usd

    egress_usd, free_gb, cross_usd = await asyncio.to_thread(_fetch_egress_rates_aws)
    await ingest_egress_from_usd(db, "aws", egress_usd, free_gb, cross_usd)
