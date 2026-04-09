"""Instance type list endpoint."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._pagination import PaginatedQuery
from app.api.utils import parse_id_or_slug
from app.core.database import get_db
from app.core.security import verify_api_key
from app.models import InstanceType, Provider
from app.schemas.common import PaginatedResponse
from app.schemas.instance_type import InstanceTypeResponse

router = APIRouter(prefix="/instance-types", tags=["instance-types"])


@router.get("", response_model=PaginatedResponse[InstanceTypeResponse])
async def list_instance_types(
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(verify_api_key),
    provider: str | None = Query(None, description="Provider slug or id"),
    min_vcpu: int | None = Query(None, ge=1),
    min_ram_gb: float | None = Query(None, gt=0),
    arch: Literal["x86_64", "arm64"] | None = Query(None, description="CPU architecture filter"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[InstanceTypeResponse]:
    """List instance types with filters and pagination."""
    base = select(InstanceType).join(Provider, InstanceType.provider_id == Provider.id)
    pq = PaginatedQuery(base, InstanceType)
    pq.join_count(Provider, InstanceType.provider_id == Provider.id)

    ref = parse_id_or_slug(provider)
    if ref is not None:
        if isinstance(ref, int):
            pq.filter(InstanceType.provider_id == ref)
        else:
            pq.filter(Provider.slug == ref)

    pq.filter_if(min_vcpu, lambda v: InstanceType.vcpu >= v)
    pq.filter_if(min_ram_gb, lambda v: InstanceType.ram_gb >= v)
    pq.filter_if(arch, lambda v: InstanceType.arch == v)

    return await pq.execute(
        db, limit=limit, offset=offset,
        order_by=(InstanceType.provider_id, InstanceType.name),
    )
