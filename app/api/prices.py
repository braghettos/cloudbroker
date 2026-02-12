"""Price list endpoint."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import verify_api_key
from app.models import InstanceType, Price, Provider, Region
from app.schemas.common import PaginatedResponse
from app.schemas.price import PriceResponse

router = APIRouter(prefix="/prices", tags=["prices"])


def _parse_id_or_slug(value: str | None) -> int | str | None:
    if value is None:
        return None
    if value.isdigit():
        return int(value)
    return value


@router.get("", response_model=PaginatedResponse[PriceResponse])
async def list_prices(
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(verify_api_key),
    provider: str | None = Query(None, description="Provider slug or id"),
    region: str | None = Query(None, description="Region slug or id"),
    min_vcpu: int | None = Query(None, ge=1),
    min_ram_gb: float | None = Query(None, gt=0),
    arch: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[PriceResponse]:
    """List prices with filters and pagination."""
    # Join path: Price -> InstanceType, Price -> Region; Provider via InstanceType
    base = select(Price).join(InstanceType).join(Region).join(Provider, InstanceType.provider_id == Provider.id)
    count_base = (
        select(func.count())
        .select_from(Price)
        .join(InstanceType)
        .join(Region)
        .join(Provider, InstanceType.provider_id == Provider.id)
    )

    provider_ref = _parse_id_or_slug(provider)
    if provider_ref is not None:
        if isinstance(provider_ref, int):
            base = base.where(Provider.id == provider_ref)
            count_base = count_base.where(Provider.id == provider_ref)
        else:
            base = base.where(Provider.slug == provider_ref)
            count_base = count_base.where(Provider.slug == provider_ref)

    region_ref = _parse_id_or_slug(region)
    if region_ref is not None:
        if isinstance(region_ref, int):
            base = base.where(Region.id == region_ref)
            count_base = count_base.where(Region.id == region_ref)
        else:
            base = base.where(Region.slug == region_ref)
            count_base = count_base.where(Region.slug == region_ref)

    if min_vcpu is not None:
        base = base.where(InstanceType.vcpu >= min_vcpu)
        count_base = count_base.where(InstanceType.vcpu >= min_vcpu)
    if min_ram_gb is not None:
        base = base.where(InstanceType.ram_gb >= min_ram_gb)
        count_base = count_base.where(InstanceType.ram_gb >= min_ram_gb)
    if arch is not None:
        base = base.where(InstanceType.arch == arch)
        count_base = count_base.where(InstanceType.arch == arch)

    total = (await db.execute(count_base)).scalar_one()
    stmt = base.order_by(Price.timestamp.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    items = result.scalars().unique().all()

    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)
