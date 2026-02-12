"""Test API authentication and rate limiting."""

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.fixture
async def client():
    """Create async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def api_key():
    """Test API key."""
    return "test-api-key-12345"


@pytest.mark.asyncio
async def test_health_endpoint_no_auth_required(client):
    """Health endpoint should be accessible without authentication."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "checks" in data
    assert data["status"] in ["ok", "degraded", "down"]


@pytest.mark.asyncio
async def test_api_endpoints_require_auth(client, monkeypatch):
    """All /api/* endpoints should require API key when API_KEY is set."""
    # Set API key to require authentication
    from app.core.config import settings
    monkeypatch.setattr(settings, "api_key", "required-key")
    monkeypatch.setattr(settings, "app_env", "production")

    # Test providers endpoint without API key
    response = await client.get("/api/providers")
    assert response.status_code == 401
    assert "Missing API key" in response.json()["detail"]


@pytest.mark.asyncio
async def test_api_endpoints_with_invalid_key(client, monkeypatch):
    """API endpoints should reject invalid API keys."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "api_key", "correct-key")
    monkeypatch.setattr(settings, "app_env", "production")

    response = await client.get("/api/providers", headers={"X-API-Key": "invalid-key"})
    assert response.status_code == 403
    assert "Invalid API key" in response.json()["detail"]


@pytest.mark.asyncio
async def test_api_endpoints_with_valid_key(client, api_key, monkeypatch):
    """API endpoints should accept valid API keys."""
    # Set API key in settings
    from app.core.config import settings
    monkeypatch.setattr(settings, "api_key", api_key)
    monkeypatch.setattr(settings, "app_env", "production")

    # Test with valid API key
    response = await client.get("/api/providers", headers={"X-API-Key": api_key})
    # Should succeed (200) or fail with 500 if DB not available, but not 401/403
    assert response.status_code != 401
    assert response.status_code != 403


@pytest.mark.asyncio
async def test_development_mode_bypasses_auth(client, monkeypatch):
    """In development mode, API key auth should be bypassed if API_KEY is not set."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    # Should work without API key in dev mode when API_KEY is not set
    response = await client.get("/api/providers")
    # Should succeed (200) or fail with 500 if DB not available, but not 401
    assert response.status_code != 401


def test_rate_limiting_config(monkeypatch):
    """Test that rate limiting middleware is configured."""
    from app.core.config import settings
    from app.main import app

    # Verify rate limit config exists
    assert hasattr(settings, "rate_limit_per_minute")
    assert settings.rate_limit_per_minute > 0

    # Verify limiter is attached to app
    assert hasattr(app.state, "limiter")
