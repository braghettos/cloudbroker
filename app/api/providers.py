"""Provider list endpoint."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import verify_api_key
from app.models import Provider
from app.schemas.common import PaginatedResponse
from app.schemas.provider import ProviderResponse

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("", response_model=PaginatedResponse[ProviderResponse])
async def list_providers(
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(verify_api_key),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[ProviderResponse]:
    """List providers with pagination."""
    count_stmt = select(func.count()).select_from(Provider)
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = select(Provider).order_by(Provider.slug).limit(limit).offset(offset)
    result = await db.execute(stmt)
    items = result.scalars().all()

    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)
