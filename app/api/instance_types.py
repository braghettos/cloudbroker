"""Instance type list endpoint."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import verify_api_key
from app.models import InstanceType, Provider
from app.schemas.common import PaginatedResponse
from app.schemas.instance_type import InstanceTypeResponse

router = APIRouter(prefix="/instance-types", tags=["instance-types"])


def _parse_provider_ref(provider: str | None) -> int | str | None:
    if provider is None:
        return None
    if provider.isdigit():
        return int(provider)
    return provider


@router.get("", response_model=PaginatedResponse[InstanceTypeResponse])
async def list_instance_types(
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(verify_api_key),
    provider: str | None = Query(None, description="Provider slug or id"),
    min_vcpu: int | None = Query(None, ge=1),
    min_ram_gb: float | None = Query(None, gt=0),
    arch: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[InstanceTypeResponse]:
    """List instance types with filters and pagination."""
    base = select(InstanceType).join(Provider, InstanceType.provider_id == Provider.id)
    count_base = (
        select(func.count())
        .select_from(InstanceType)
        .join(Provider, InstanceType.provider_id == Provider.id)
    )

    ref = _parse_provider_ref(provider)
    if ref is not None:
        if isinstance(ref, int):
            base = base.where(InstanceType.provider_id == ref)
            count_base = count_base.where(InstanceType.provider_id == ref)
        else:
            base = base.where(Provider.slug == ref)
            count_base = count_base.where(Provider.slug == ref)

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
    stmt = base.order_by(InstanceType.provider_id, InstanceType.name).limit(limit).offset(offset)
    result = await db.execute(stmt)
    items = result.scalars().unique().all()

    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)
