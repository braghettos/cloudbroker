"""Pytest configuration and fixtures for async tests."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, engine
from app.main import app


@pytest.fixture(autouse=True, scope="function")
async def cleanup_db_connections():
    """Ensure database connections are properly closed after each test."""
    yield
    await engine.dispose(close=True)


@pytest.fixture
async def db_session():
    """Yield an async DB session that rolls back after each test."""
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def client():
    """Create async test client for the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def dev_mode(monkeypatch):
    """Patch settings to development mode with no API key."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)


@pytest.fixture
async def seed_provider(db_session: AsyncSession):
    """Factory fixture: create a test provider."""
    from app.models.provider import Provider, ProviderType

    async def _create(
        slug: str = "test-provider",
        name: str = "Test Provider",
        provider_type: ProviderType = ProviderType.EU,
    ) -> Provider:
        p = Provider(slug=slug, name=name, type=provider_type)
        db_session.add(p)
        await db_session.flush()
        return p

    return _create


@pytest.fixture
async def seed_region(db_session: AsyncSession):
    """Factory fixture: create a test region."""
    from app.models.region import Region

    async def _create(
        provider_id: int,
        slug: str = "eu-test-1",
        name: str = "EU Test 1",
        country: str = "DE",
        is_eu: bool = True,
    ) -> Region:
        r = Region(provider_id=provider_id, slug=slug, name=name, country=country, is_eu=is_eu)
        db_session.add(r)
        await db_session.flush()
        return r

    return _create


@pytest.fixture
async def seed_instance_type(db_session: AsyncSession):
    """Factory fixture: create a test instance type."""
    from app.models.instance_type import InstanceType

    async def _create(
        provider_id: int,
        name: str = "t3.medium",
        vcpu: int = 2,
        ram_gb: float = 4.0,
        arch: str = "x86_64",
        family: str | None = None,
    ) -> InstanceType:
        it = InstanceType(
            provider_id=provider_id,
            name=name,
            vcpu=vcpu,
            ram_gb=ram_gb,
            arch=arch,
            family=family,
        )
        db_session.add(it)
        await db_session.flush()
        return it

    return _create
