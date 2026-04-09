"""AWS storage rate connector: fetch EBS pricing from Pricing API."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from connectors._cloud_clients import get_aws_pricing_client

logger = get_logger(__name__)

_REP_REGION = "EU (Ireland)"  # eu-west-1


def _fetch_ebs_products_sync() -> list[dict]:
    """Fetch EBS storage products from GetProducts."""
    client = get_aws_pricing_client()
    products: list[dict] = []
    next_token = None
    filters = [
        {"Type": "TERM_MATCH", "Field": "location", "Value": _REP_REGION},
        {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Storage"},
    ]
    while True:
        kw: dict[str, Any] = {"ServiceCode": "AmazonEC2", "Filters": filters, "MaxResults": 100}
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


def _extract_storage_usd(product: dict) -> tuple[str, float] | None:
    """Extract (storage_type, usd_per_gb_month) from EBS product."""
    try:
        attrs = product.get("attributes") or {}
        vol_type = (attrs.get("volumeApiName") or attrs.get("storageMedia") or "").lower()
        if not vol_type or "snapshot" in vol_type or "backup" in vol_type:
            return None
        type_map = {"gp3": "ssd", "gp2": "ssd", "io1": "ssd", "io2": "ssd", "st1": "hdd", "sc1": "hdd"}
        storage_type = type_map.get(vol_type, "balanced")
        terms = product.get("terms") or {}
        on_demand = terms.get("OnDemand", {})
        for sku_data in on_demand.values():
            dims = sku_data.get("priceDimensions", {})
            for dim in dims.values():
                unit = (dim.get("unit") or "").lower()
                if "gb-mo" not in unit and "gb-month" not in unit:
                    continue
                price_per = dim.get("pricePerUnit") or {}
                usd = price_per.get("USD")
                if usd is not None:
                    return (storage_type, float(usd))
        return None
    except (TypeError, ValueError, KeyError):
        return None


def _fetch_storage_rates_aws() -> list[tuple[str, float]]:
    """Returns list of (storage_type, usd_per_gb_month)."""
    products = _fetch_ebs_products_sync()
    seen: set[str] = set()
    result: list[tuple[str, float]] = []
    for p in products:
        extracted = _extract_storage_usd(p)
        if extracted and extracted[0] not in seen:
            seen.add(extracted[0])
            result.append(extracted)
    if not result:
        logger.warning("AWS storage: no EBS products found")
        return [("ssd", 0.08), ("hdd", 0.045), ("balanced", 0.065)]
    return result


async def ingest_storage_aws(db: AsyncSession) -> None:
    """Ingest AWS storage rates."""
    from connectors._storage_helper import ingest_storage_from_usd

    rates = await asyncio.to_thread(_fetch_storage_rates_aws)
    await ingest_storage_from_usd(db, "aws", rates, region_slug="eu-west-1")
