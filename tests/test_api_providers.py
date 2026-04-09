"""Integration tests for providers API endpoint."""

import pytest


@pytest.mark.asyncio
async def test_get_providers_without_auth(client, monkeypatch):
    """Test providers endpoint without API key (dev mode)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    response = await client.get("/api/providers")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "limit" in data
    assert "offset" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_get_providers_pagination(client, monkeypatch):
    """Test providers endpoint pagination."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    # Test with limit
    response = await client.get("/api/providers?limit=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) <= 1
    assert data["limit"] == 1

    # Test with offset
    response = await client.get("/api/providers?limit=1&offset=1")
    assert response.status_code == 200
    data = response.json()
    assert data["offset"] == 1


@pytest.mark.asyncio
async def test_get_providers_invalid_limit(client, monkeypatch):
    """Test providers endpoint with invalid limit."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    # Test with limit too high
    response = await client.get("/api/providers?limit=200")
    assert response.status_code == 422  # Validation error

    # Test with limit too low
    response = await client.get("/api/providers?limit=0")
    assert response.status_code == 422  # Validation error
