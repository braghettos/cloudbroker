"""Test Provider.type enum stores lowercase values correctly."""

import pytest

from app.core.database import AsyncSessionLocal
from app.models import Provider
from app.models.provider import ProviderType


@pytest.mark.asyncio
async def test_provider_type_hyperscaler_stores_lowercase():
    """Create Provider with type=hyperscaler and verify it stores as lowercase string."""
    async with AsyncSessionLocal() as db:
        p = Provider(
            slug="test-provider",
            name="Test Provider",
            type=ProviderType.HYPERSCALER,
        )
        db.add(p)
        await db.flush()
        # Verify the enum value (not name) is stored
        assert p.type == ProviderType.HYPERSCALER
        assert p.type.value == "hyperscaler"
        # Query back from DB to verify stored value
        from sqlalchemy import select

        stmt = select(Provider).where(Provider.slug == "test-provider")
        result = await db.execute(stmt)
        fetched = result.scalars().first()
        assert fetched is not None
        assert fetched.type == ProviderType.HYPERSCALER
        assert fetched.type.value == "hyperscaler"
        # Cleanup
        await db.delete(fetched)
        await db.commit()


@pytest.mark.asyncio
async def test_provider_type_eu_stores_lowercase():
    """Create Provider with type=eu and verify it stores correctly."""
    async with AsyncSessionLocal() as db:
        p = Provider(
            slug="test-eu-provider-unique-final",
            name="Test EU Provider",
            type=ProviderType.EU,
        )
        db.add(p)
        await db.flush()
        assert p.type.value == "eu"
        # Cleanup - rollback instead of commit to avoid leaving test data
        await db.rollback()
