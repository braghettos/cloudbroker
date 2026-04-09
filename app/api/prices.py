"""Price list endpoint."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._pagination import PaginatedQuery
from app.api.utils import parse_id_or_slug
from app.core.database import get_db
from app.core.security import verify_api_key
from app.models import InstanceType, Price, Provider, Region
from app.schemas.common import PaginatedResponse
from app.schemas.price import PriceResponse

router = APIRouter(prefix="/prices", tags=["prices"])


@router.get("", response_model=PaginatedResponse[PriceResponse])
async def list_prices(
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(verify_api_key),
    provider: str | None = Query(None, description="Provider slug or id"),
    region: str | None = Query(None, description="Region slug or id"),
    min_vcpu: int | None = Query(None, ge=1),
    min_ram_gb: float | None = Query(None, gt=0),
    arch: Literal["x86_64", "arm64"] | None = Query(None, description="CPU architecture filter"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[PriceResponse]:
    """List prices with filters and pagination."""
    base = select(Price).join(InstanceType).join(Region).join(Provider, InstanceType.provider_id == Provider.id)
    pq = PaginatedQuery(base, Price)
    pq.join_count(InstanceType).join_count(Region).join_count(Provider, InstanceType.provider_id == Provider.id)

    provider_ref = parse_id_or_slug(provider)
    if provider_ref is not None:
        if isinstance(provider_ref, int):
            pq.filter(Provider.id == provider_ref)
        else:
            pq.filter(Provider.slug == provider_ref)

    region_ref = parse_id_or_slug(region)
    if region_ref is not None:
        if isinstance(region_ref, int):
            pq.filter(Region.id == region_ref)
        else:
            pq.filter(Region.slug == region_ref)

    pq.filter_if(min_vcpu, lambda v: InstanceType.vcpu >= v)
    pq.filter_if(min_ram_gb, lambda v: InstanceType.ram_gb >= v)
    pq.filter_if(arch, lambda v: InstanceType.arch == v)

    return await pq.execute(db, limit=limit, offset=offset, order_by=Price.timestamp.desc())
