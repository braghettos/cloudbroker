"""Reusable paginated query builder for list endpoints.

Eliminates the duplicated pattern of maintaining parallel base + count_base
statements with identical filter conditions across providers, regions,
instance_types, and prices routers.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import PaginatedResponse


class PaginatedQuery:
    """Builder for paginated list queries with automatic count tracking.

    Usage:
        pq = PaginatedQuery(select(Provider), Provider)
        pq.filter(Provider.slug == "aws")
        return await pq.execute(db, limit=20, offset=0, order_by=Provider.slug)
    """

    def __init__(self, base_stmt: Select, count_from: Any) -> None:
        """
        Args:
            base_stmt: The SELECT statement for fetching items.
            count_from: The model or subquery to count from (used in select(func.count()).select_from(...)).
        """
        self._base = base_stmt
        self._count = select(func.count()).select_from(count_from)
        # Track joins applied to count (count_from may need same joins as base)
        self._count_joins: list = []

    def join_count(self, target: Any, onclause: Any = None) -> PaginatedQuery:
        """Add a join to the count query (when the base already has it via select_from)."""
        if onclause is not None:
            self._count = self._count.join(target, onclause)
        else:
            self._count = self._count.join(target)
        return self

    def filter(self, *conditions: Any) -> PaginatedQuery:
        """Apply filter conditions to both base and count queries."""
        for cond in conditions:
            self._base = self._base.where(cond)
            self._count = self._count.where(cond)
        return self

    def filter_if(self, value: Any, condition_fn: Any) -> PaginatedQuery:
        """Apply filter only if value is not None."""
        if value is not None:
            cond = condition_fn(value)
            self._base = self._base.where(cond)
            self._count = self._count.where(cond)
        return self

    async def execute(
        self,
        db: AsyncSession,
        limit: int,
        offset: int,
        order_by: Any = None,
    ) -> PaginatedResponse:
        """Execute both count and data queries, return PaginatedResponse."""
        total = (await db.execute(self._count)).scalar_one()

        stmt = self._base
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        stmt = stmt.limit(limit).offset(offset)

        result = await db.execute(stmt)
        items = result.scalars().unique().all()

        return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)
