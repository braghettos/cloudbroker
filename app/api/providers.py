"""Provider list endpoint."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._pagination import PaginatedQuery
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
    pq = PaginatedQuery(select(Provider), Provider)
    return await pq.execute(db, limit=limit, offset=offset, order_by=Provider.slug)
