"""IBM Cloud VPC connector: fetch instance profiles from VPC API, pricing from Global Catalog."""

from __future__ import annotations

import os
from decimal import Decimal
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur, to_eur
from app.core.logging import get_logger
from app.models.price import PurchaseModel
from app.models.provider import ProviderType
from app.services.ingestion import (
    append_price,
    ensure_instance_type,
    ensure_provider,
    ensure_region,
)

logger = get_logger(__name__)

IAM_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"
VPC_API_VERSION = "2025-06-01"
GLOBAL_CATALOG_BASE = "https://globalcatalog.cloud.ibm.com/api/v1"
# Standard Gen2 plan (current VPC instance plan)
PLAN_ID = "a736a57f-0584-474f-8411-55dc7d9dc811"

# Region slug -> (display name, country code, is_eu)
REGIONS: dict[str, tuple[str, str, bool]] = {
    "au-syd": ("Sydney", "AU", False),
    "br-sao": ("São Paulo", "BR", False),
    "ca-tor": ("Toronto", "CA", False),
    "eu-de": ("Frankfurt", "DE", True),
    "eu-es": ("Madrid", "ES", True),
    "eu-gb": ("London", "GB", True),
    "jp-osa": ("Osaka", "JP", False),
    "jp-tok": ("Tokyo", "JP", False),
    "us-east": ("Washington DC", "US", False),
    "us-south": ("Dallas", "US", False),
}

# Sanity limit: skip profiles with hourly price > $50 per vCPU
MAX_USD_PER_VCPU_PER_HOUR = 50.0


async def _get_iam_token(api_key: str) -> str:
    """Exchange IBM Cloud API key for IAM bearer token."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            IAM_TOKEN_URL,
            data={
                "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                "apikey": api_key,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        data = r.json()
    return str(data["access_token"])


async def _fetch_profiles(token: str, region: str) -> list[dict]:
    """Fetch instance profiles from VPC API for a region."""
    base = f"https://{region}.iaas.cloud.ibm.com"
    profiles: list[dict] = []
    start = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            params: dict = {"version": VPC_API_VERSION, "generation": 2, "limit": 100}
            if start:
                params["start"] = start
            r = await client.get(
                f"{base}/v1/instance/profiles",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            r.raise_for_status()
            data = r.json()
            profs = data.get("profiles") or []
            profiles.extend(profs)
            start = data.get("next", {}).get("start")
            if not start:
                break
    return profiles


async def _fetch_pricing_rates(token: str | None = None) -> dict[str, dict[str, float]]:
    """
    Fetch per-unit CPU and RAM rates from Global Catalog per region.
    Returns {region_slug: {"cpu_rate": USD/vCPU-hr, "ram_rate": USD/GB-hr}}.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # List plan children to get region-level deployment IDs
    deployments: list[tuple[str, str]] = []  # (region_slug, deployment_id_suffix)
    offset = 0
    total_count = 0
    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            r = await client.get(
                f"{GLOBAL_CATALOG_BASE}/{PLAN_ID}/%2A",
                params={"_offset": offset, "depth": 0, "limit": 100},
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
            resources = data.get("resources") or []
            total_count = data.get("count") or 0
            for res in resources:
                loc = (res.get("metadata") or {}).get("deployment", {}).get("location", "")
                rid = res.get("id", "")
                if not loc or not rid or ":" not in rid:
                    continue
                suffix = rid.split(":", 1)[1]
                # Region-level: location matches REGIONS keys (no zone -1, -2, -3)
                if loc in REGIONS:
                    deployments.append((loc, suffix))
            offset += len(resources)
            if not resources or offset >= total_count:
                break

    # Deduplicate by region (keep first)
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for reg, suf in deployments:
        if reg not in seen:
            seen.add(reg)
            unique.append((reg, suf))

    rates: dict[str, dict[str, float]] = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for region_slug, dep_suffix in unique:
            dep_id = f"{PLAN_ID}:{dep_suffix}"
            dep_id_encoded = dep_id.replace(":", "%3A")
            try:
                r = await client.get(
                    f"{GLOBAL_CATALOG_BASE}/{dep_id_encoded}/pricing",
                    headers=headers,
                )
                if r.status_code != 200:
                    continue
                data = r.json()
            except Exception as e:
                logger.warning("IBM Cloud pricing fetch failed", region=region_slug, error=str(e))
                continue

            cpu_rate: float | None = None
            ram_rate: float | None = None
            for m in data.get("metrics") or []:
                mid = m.get("metric_id", "")
                qty = float(m.get("charge_unit_quantity") or 1)
                if qty <= 0:
                    continue
                for a in m.get("amounts") or []:
                    if a.get("currency") != "USD":
                        continue
                    prices = a.get("prices") or []
                    if not prices:
                        continue
                    price_val = float(prices[0].get("price", 0))
                    per_unit = price_val / qty
                    if "cpu" in mid.lower() or mid == "part-is.cpu-hours":
                        cpu_rate = per_unit
                    elif "ram" in mid.lower() or "memory" in mid.lower() or mid == "part-is.ram-hours":
                        ram_rate = per_unit
                    break

            if cpu_rate is not None and ram_rate is not None:
                rates[region_slug] = {"cpu_rate": cpu_rate, "ram_rate": ram_rate}
            else:
                logger.debug("Incomplete pricing for region", region=region_slug, cpu_rate=cpu_rate, ram_rate=ram_rate)

    return rates


def _arch(raw: str | None) -> str:
    if not raw:
        return "x86_64"
    v = (raw or "").lower()
    return "arm64" if v == "arm" else "x86_64"


async def ingest(
    db: AsyncSession,
    only_eu: bool = True,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest IBM Cloud VPC instance profiles and pricing."""
    from app.core.config import settings

    api_key = settings.ibmcloud_api_key or os.environ.get("IBMCLOUD_API_KEY")
    if not api_key:
        raise RuntimeError(
            "IBMCLOUD_API_KEY environment variable is required for IBM Cloud ingestion"
        )

    purchase = PurchaseModel(purchase_model)
    if purchase != PurchaseModel.ON_DEMAND:
        logger.warning(
            "IBM Cloud VPC only supports on-demand pricing, ignoring purchase_model",
            purchase_model=purchase_model,
        )
        purchase = PurchaseModel.ON_DEMAND

    token = await _get_iam_token(api_key)
    pricing_rates = await _fetch_pricing_rates(token=token)
    if not pricing_rates:
        logger.warning("No IBM Cloud pricing rates fetched; ingestion will have no prices")

    provider = await ensure_provider(
        db,
        slug="ibm_cloud",
        name="IBM Cloud",
        website="https://www.ibm.com/cloud",
        type=ProviderType.HYPERSCALER,
    )
    await db.flush()

    region_by_slug: dict[str, object] = {}
    for slug, (name, country, is_eu) in REGIONS.items():
        if only_eu and not is_eu:
            continue
        r = await ensure_region(
            db,
            provider_id=provider.id,
            slug=slug,
            name=name,
            country=country,
            is_eu=is_eu,
        )
        region_by_slug[slug] = r
    await db.flush()

    from sqlalchemy import select
    from app.models import Region

    regions_stmt = select(Region).where(Region.provider_id == provider.id)
    regions_result = await db.execute(regions_stmt)
    region_by_slug = {r.slug: r for r in regions_result.scalars().all()}

    rate_usd_eur = await get_rate_to_eur(db, "USD")
    inserted_prices = 0

    for region_slug in region_by_slug:
        if region_slug not in pricing_rates:
            continue
        rates = pricing_rates[region_slug]
        cpu_rate = rates["cpu_rate"]
        ram_rate = rates["ram_rate"]

        try:
            profiles = await _fetch_profiles(token, region_slug)
        except Exception as e:
            logger.warning("VPC profiles fetch failed", region=region_slug, error=str(e))
            continue

        region = region_by_slug[region_slug]
        for p in profiles:
            if (p.get("status") or "").lower() != "current":
                continue
            name = (p.get("name") or "").strip()
            if not name:
                continue

            vcpu_obj = p.get("vcpu_count") or {}
            vcpu = int(vcpu_obj.get("value", 0)) if isinstance(vcpu_obj, dict) else int(vcpu_obj or 0)
            mem_obj = p.get("memory") or {}
            ram_gb = float(mem_obj.get("value", 0)) if isinstance(mem_obj, dict) else float(mem_obj or 0)
            if vcpu <= 0 or ram_gb <= 0:
                continue

            hourly_usd = (vcpu * cpu_rate) + (ram_gb * ram_rate)
            if hourly_usd <= 0:
                continue
            if hourly_usd / vcpu > MAX_USD_PER_VCPU_PER_HOUR:
                logger.warning(
                    "Skipping profile with unreasonably high price",
                    profile=name,
                    region=region_slug,
                    hourly_usd=round(hourly_usd, 4),
                    vcpu=vcpu,
                )
                continue

            arch_raw = None
            ac = p.get("vcpu_architecture")
            if isinstance(ac, dict):
                arch_raw = ac.get("value")
            elif ac:
                arch_raw = str(ac)
            arch = _arch(arch_raw)
            family = (p.get("family") or "").strip() or None
            bw_obj = p.get("bandwidth") or {}
            bw_val = bw_obj.get("value") if isinstance(bw_obj, dict) else bw_obj
            network_perf = str(bw_val) + " Gbps" if bw_val else None

            it = await ensure_instance_type(
                db,
                provider_id=provider.id,
                name=name,
                vcpu=vcpu,
                ram_gb=ram_gb,
                family=family,
                arch=arch,
                network_performance=network_perf,
                is_current_gen=True,
            )
            await db.flush()

            price_eur = to_eur(hourly_usd, rate_usd_eur)
            price_row = await append_price(
                db,
                instance_type_id=it.id,
                region_id=region.id,
                purchase_model=purchase,
                currency="USD",
                price_per_hour=round(hourly_usd, 6),
                price_eur_per_hour=round(price_eur, 6),
                source_currency="USD",
            )
            if price_row:
                inserted_prices += 1

    logger.info(
        "IBM Cloud ingestion completed",
        regions=len(region_by_slug),
        price_rows_inserted=inserted_prices,
    )
