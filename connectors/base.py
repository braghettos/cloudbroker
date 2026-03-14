"""Base protocol for provider connectors."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession


@runtime_checkable
class ConnectorProtocol(Protocol):
    """All provider connectors must expose an ``ingest`` coroutine."""

    async def ingest(
        self,
        db: AsyncSession,
        only_eu: bool = True,
        purchase_model: str = "on_demand",
    ) -> None: ...
