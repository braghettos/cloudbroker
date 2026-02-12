"""Region list endpoint."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import verify_api_key
from app.models import Provider, Region
from app.schemas.common import PaginatedResponse
from app.schemas.region import RegionResponse

router = APIRouter(prefix="/regions", tags=["regions"])


def _parse_provider_ref(provider: str | None) -> int | str | None:
    """Return provider id (int) or slug (str) for filtering."""
    if provider is None:
        return None
    if provider.isdigit():
        return int(provider)
    return provider


@router.get("", response_model=PaginatedResponse[RegionResponse])
async def list_regions(
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(verify_api_key),
    provider: str | None = Query(None, description="Provider slug or id"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[RegionResponse]:
    """List regions with optional provider filter and pagination."""
    base = select(Region)
    count_base = select(func.count()).select_from(Region)

    ref = _parse_provider_ref(provider)
    if ref is not None:
        if isinstance(ref, int):
            base = base.where(Region.provider_id == ref)
            count_base = count_base.where(Region.provider_id == ref)
        else:
            base = base.join(Provider).where(Provider.slug == ref)
            count_base = count_base.join(Provider).where(Provider.slug == ref)

    total = (await db.execute(count_base)).scalar_one()
    stmt = base.order_by(Region.provider_id, Region.slug).limit(limit).offset(offset)
    result = await db.execute(stmt)
    items = result.scalars().unique().all()

    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)
