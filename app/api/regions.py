"""Region list and sustainability endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._pagination import PaginatedQuery
from app.api.utils import parse_id_or_slug
from app.core.constants import PROVIDERS_PUBLISH_CARBON
from app.core.database import get_db
from app.core.security import verify_api_key
from app.models import Provider, Region
from app.schemas.common import PaginatedResponse
from app.schemas.region import RegionResponse
from app.services.sustainability_service import SustainabilityData, get_current_sustainability

router = APIRouter(prefix="/regions", tags=["regions"])


class SustainabilityResponse(BaseModel):
    """Real-time sustainability data for a region."""

    provider_slug: str
    region_slug: str
    carbon_intensity_g_co2_kwh: float = Field(..., description="Grid carbon intensity (gCO2eq/kWh)")
    renewable_energy_pct: float = Field(..., ge=0, le=1, description="Renewable energy share 0-1")
    source: str = Field(..., description="Data source: entsoe, uk_api, eia, static, config, db")
    # DB-stored values (from batch ingestion; may differ from real-time)
    db_carbon_intensity_g_co2_kwh: float | None = Field(None, description="DB-stored carbon intensity (batch ingested)")
    db_renewable_energy_pct: float | None = Field(None, description="DB-stored renewable energy pct (batch ingested)")


@router.get("", response_model=PaginatedResponse[RegionResponse])
async def list_regions(
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(verify_api_key),
    provider: str | None = Query(None, description="Provider slug or id"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    min_renewable_pct: float | None = Query(None, ge=0, le=1, description="Minimum renewable energy share (0-1)"),
    providers_publish_carbon_only: bool = Query(
        False, description="Restrict to providers that publish carbon emissions"
    ),
    max_carbon_intensity: float | None = Query(None, ge=0, description="Max carbon intensity (gCO2eq/kWh)"),
) -> PaginatedResponse[RegionResponse]:
    """List regions with optional provider and sustainability filters."""
    base = select(Region).join(Provider, Region.provider_id == Provider.id)
    pq = PaginatedQuery(base, Region)
    pq.join_count(Provider, Region.provider_id == Provider.id)

    ref = parse_id_or_slug(provider)
    if ref is not None:
        if isinstance(ref, int):
            pq.filter(Region.provider_id == ref)
        else:
            pq.filter(Provider.slug == ref)

    if providers_publish_carbon_only:
        pq.filter(Provider.slug.in_(PROVIDERS_PUBLISH_CARBON))

    if min_renewable_pct is not None and min_renewable_pct > 0:
        pq.filter(Region.renewable_energy_pct >= min_renewable_pct)

    if max_carbon_intensity is not None:
        pq.filter(Region.carbon_intensity_g_co2_kwh <= max_carbon_intensity)

    return await pq.execute(
        db, limit=limit, offset=offset,
        order_by=(Region.provider_id, Region.slug),
    )


@router.get("/{region_id}/sustainability", response_model=SustainabilityResponse)
async def get_region_sustainability(
    region_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(verify_api_key),
) -> SustainabilityResponse:
    """Get real-time sustainability data for a region.

    Returns the current carbon intensity (gCO2eq/kWh) and renewable energy share
    for the region using the highest-priority available data source:
      - EU regions: ENTSO-E real-time grid data (requires ENTSOE_TOKEN)
      - UK regions: UK Carbon Intensity API (free, no auth)
      - US regions: EIA OpenData API (requires EIA_API_KEY)
      - All others: Ember Climate country-level yearly average (static)

    Also returns the DB-stored batch-ingested values for comparison.
    """
    ref = parse_id_or_slug(region_id)
    stmt = (
        select(Region, Provider.slug.label("provider_slug"))
        .join(Provider, Region.provider_id == Provider.id)
    )
    if isinstance(ref, int):
        stmt = stmt.where(Region.id == ref)
    else:
        stmt = stmt.where(Region.slug == ref)

    result = await db.execute(stmt)
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Region not found")

    region: Region = row[0]
    provider_slug: str = row[1]

    # Fetch real-time sustainability data
    data: SustainabilityData | None = await get_current_sustainability(
        provider_slug=provider_slug,
        region_slug=region.slug,
        country_code=region.country,
    )

    if data is None:
        # Fall back to DB values if real-time and static both return None
        if region.carbon_intensity_g_co2_kwh is None and region.renewable_energy_pct is None:
            raise HTTPException(
                status_code=404,
                detail="No sustainability data available for this region. "
                "Run sustainability ingestion or configure ENTSOE_TOKEN.",
            )
        data = SustainabilityData(
            carbon_intensity_g_co2_kwh=region.carbon_intensity_g_co2_kwh or 0.0,
            renewable_energy_pct=region.renewable_energy_pct or 0.0,
            source="db",
        )

    return SustainabilityResponse(
        provider_slug=provider_slug,
        region_slug=region.slug,
        carbon_intensity_g_co2_kwh=data.carbon_intensity_g_co2_kwh,
        renewable_energy_pct=data.renewable_energy_pct,
        source=data.source,
        db_carbon_intensity_g_co2_kwh=region.carbon_intensity_g_co2_kwh,
        db_renewable_energy_pct=region.renewable_energy_pct,
    )
