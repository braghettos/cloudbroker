"""Optional batch ingestion of sustainability data.

Populates Region.renewable_energy_pct and Region.carbon_intensity_g_co2_kwh
for DB fallback when real-time ENTSO-E/UK/EIA data is unavailable at query time.

Sources (all free, no commercial API required):
  1. Google region-carbon-info CSV (GCP official data, yearly)
  2. ENTSO-E config overrides (config/sustainability.yaml region_overrides)
  3. Ember Climate country-level averages (embedded static data, yearly)

The embedded Ember data is used for all non-GCP regions that are not covered
by the YAML overrides. It provides a country-level baseline that is much better
than having no data at all, enabling downstream sustainability filtering and scoring.
"""

from __future__ import annotations

import csv
from urllib.request import urlopen

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Provider, Region
from app.services.sustainability_service import (
    COUNTRY_STATIC_INTENSITY,
    _SLUG_COUNTRY_HINTS,
    _get_config,
)

logger = get_logger(__name__)

GCP_REGION_CARBON_URL = (
    "https://raw.githubusercontent.com/GoogleCloudPlatform/region-carbon-info"
    "/main/data/yearly/2024.csv"
)


async def ingest_sustainability(db: AsyncSession) -> None:
    """Ingest sustainability data for regions. Updates existing regions only.

    Execution order:
      1. GCP official CSV (overrides everything for GCP regions)
      2. YAML overrides (highest priority for any provider)
      3. Ember static data for remaining regions with no data (DB fallback population)
    """
    config = await _get_config()
    overrides = config.get("region_overrides") or {}

    # ── 1. GCP: Google region-carbon-info CSV (official, free, no auth) ─────
    try:
        with urlopen(GCP_REGION_CARBON_URL, timeout=30) as resp:
            reader = csv.DictReader(resp.read().decode().splitlines())
            gcp_provider = (
                await db.execute(select(Provider).where(Provider.slug == "gcp"))
            ).scalar_one_or_none()
            if gcp_provider:
                count = 0
                for row in reader:
                    region_slug = (row.get("Google Cloud Region") or "").strip().lower().replace("_", "-")
                    if not region_slug:
                        continue
                    cfe = row.get("Google CFE")
                    grid_carbon = row.get("Grid carbon intensity (gCO2eq / kWh)")
                    renewable = float(cfe) if cfe else None
                    carbon = float(grid_carbon) if grid_carbon else None
                    region = (
                        await db.execute(
                            select(Region).where(
                                Region.provider_id == gcp_provider.id,
                                Region.slug == region_slug,
                            )
                        )
                    ).scalar_one_or_none()
                    if region:
                        region.renewable_energy_pct = renewable
                        region.carbon_intensity_g_co2_kwh = carbon
                        region.publishes_carbon_data = renewable is not None or carbon is not None
                        count += 1
                await db.flush()
                logger.info("GCP sustainability ingested", regions_updated=count)
    except Exception as e:
        logger.warning("GCP sustainability ingestion failed", error=str(e))

    # ── 2. YAML overrides (any provider, highest priority) ───────────────────
    for provider_slug, regions in overrides.items():
        prov = (
            await db.execute(select(Provider).where(Provider.slug == provider_slug))
        ).scalar_one_or_none()
        if not prov:
            continue
        for region_slug, data in (regions or {}).items():
            if not isinstance(data, dict):
                continue
            renewable = data.get("renewable_energy_pct")
            carbon = data.get("carbon_intensity_g_co2_kwh")
            region = (
                await db.execute(
                    select(Region).where(
                        Region.provider_id == prov.id,
                        Region.slug == region_slug,
                    )
                )
            ).scalar_one_or_none()
            if region:
                region.renewable_energy_pct = float(renewable) if renewable is not None else None
                region.carbon_intensity_g_co2_kwh = float(carbon) if carbon is not None else None
                region.publishes_carbon_data = True
        logger.info("Sustainability overrides applied", provider=provider_slug)

    # ── 3. Ember Climate static fallback for remaining regions ───────────────
    # For every region without sustainability data, try to infer country code
    # from the region slug and populate with Ember yearly country averages.
    # This provides a baseline DB fallback for non-EU regions that have no
    # ENTSO-E mapping and no YAML override (e.g., US, APAC, LatAm regions).
    try:
        stmt = select(Provider.slug, Region).join(Region, Region.provider_id == Provider.id)
        result = await db.execute(stmt)
        ember_count = 0
        for row in result.all():
            provider_slug_db: str = row[0]
            region: Region = row[1]

            # Skip if already has data (from GCP CSV or YAML override)
            if region.renewable_energy_pct is not None or region.carbon_intensity_g_co2_kwh is not None:
                continue

            # Infer country code
            region_slug_lower = (region.slug or "").lower()
            country_code = _SLUG_COUNTRY_HINTS.get(region_slug_lower)

            # Try provider-prefixed slugs (e.g. "us-east-1" → already in hints)
            if country_code is None:
                # Try common suffixes/prefixes
                country_code = _infer_country_heuristic(provider_slug_db, region_slug_lower)

            if country_code is None:
                continue

            ember_entry = COUNTRY_STATIC_INTENSITY.get(country_code.upper())
            if ember_entry is None:
                continue

            region.carbon_intensity_g_co2_kwh = ember_entry[0]
            region.renewable_energy_pct = ember_entry[1]
            region.publishes_carbon_data = True
            ember_count += 1

        if ember_count > 0:
            await db.flush()
            logger.info("Ember static sustainability applied", regions_updated=ember_count)
    except Exception as e:
        logger.warning("Ember static ingestion failed", error=str(e))

    # ── 4. Mark providers that publish carbon emissions ──────────────────────
    for slug in ("gcp", "aws", "azure", "upcloud"):
        prov = (
            await db.execute(select(Provider).where(Provider.slug == slug))
        ).scalar_one_or_none()
        if prov:
            prov.publishes_carbon_emissions = True
    await db.flush()
    logger.info("Sustainability batch ingestion complete")


def _infer_country_heuristic(provider_slug: str, region_slug: str) -> str | None:
    """Infer country code from provider + region slug patterns not in the static hints.

    Covers common slug conventions across cloud providers.
    """
    slug = region_slug.lower()

    # AWS-style: us-*, ca-*, eu-*, ap-*, me-*, sa-*, af-*
    if slug.startswith("us-"):
        return "US"
    if slug.startswith("ca-"):
        return "CA"
    if slug.startswith("sa-"):
        return "BR"  # Only SA region currently is Brazil
    if slug.startswith("af-"):
        return "ZA"
    if slug.startswith("me-"):
        return "AE"  # UAE (me-central-1) or Israel (il-central-1)
    if slug.startswith("il-"):
        return "IL"

    # Azure-style: japaneast, koreacentral, australiaeast, brazilsouth, etc.
    if "japan" in slug:
        return "JP"
    if "korea" in slug:
        return "KR"
    if "australia" in slug:
        return "AU"
    if "brazil" in slug:
        return "BR"
    if "india" in slug:
        return "IN"
    if "china" in slug or slug.startswith("cn-"):
        return None  # Separate Azure China cloud — no good fallback
    if "uae" in slug or "dubai" in slug:
        return "AE"
    if "southafrica" in slug:
        return "ZA"
    if "israel" in slug:
        return "IL"
    if "canada" in slug:
        return "CA"
    if "mexico" in slug:
        return "MX"
    if "newzealand" in slug or "nz-" in slug:
        return "NZ"

    # Singapore / Southeast Asia
    if "singapore" in slug or slug.startswith("sgp") or slug in ("ap-southeast-1",):
        return "SG"
    # Hetzner US / APAC DCs
    if provider_slug == "hetzner" and slug in ("ash", "hil"):
        return "US"

    # DigitalOcean
    if provider_slug == "digitalocean":
        if slug.startswith("nyc") or slug.startswith("sfo") or slug.startswith("tor"):
            return "US" if not slug.startswith("tor") else "CA"
        if slug.startswith("sgp"):
            return "SG"
        if slug.startswith("blr"):
            return "IN"
        if slug.startswith("syd"):
            return "AU"

    return None
