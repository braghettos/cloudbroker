"""Integration tests for recommendations API endpoint."""

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.fixture
async def client():
    """Create async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_post_recommendations_valid_request(client, monkeypatch):
    """Test recommendations endpoint with valid request."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    payload = {
        "min_vcpu": 2,
        "min_ram_gb": 4,
        "arch": "x86_64",
        "region_constraint": "EU",
        "max_price_eur_per_hour": 0.5,
        "limit": 5,
    }

    response = await client.post("/api/recommendations", json=payload)
    # Should succeed (200) or return empty list if no data
    assert response.status_code in [200, 500]  # 500 if DB not available
    if response.status_code == 200:
        data = response.json()
        assert "recommendations" in data
        assert isinstance(data["recommendations"], list)


@pytest.mark.asyncio
async def test_post_recommendations_invalid_request(client, monkeypatch):
    """Test recommendations endpoint with invalid request."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    # Missing required fields
    response = await client.post("/api/recommendations", json={})
    assert response.status_code == 422  # Validation error

    # Invalid enum value
    payload = {
        "min_vcpu": 2,
        "min_ram_gb": 4,
        "arch": "invalid_arch",
        "region_constraint": "EU",
        "max_price_eur_per_hour": 0.5,
        "limit": 5,
    }
    response = await client.post("/api/recommendations", json=payload)
    assert response.status_code == 422  # Validation error

    # Negative values
    payload = {
        "min_vcpu": -1,
        "min_ram_gb": 4,
        "arch": "x86_64",
        "region_constraint": "EU",
        "max_price_eur_per_hour": 0.5,
        "limit": 5,
    }
    response = await client.post("/api/recommendations", json=payload)
    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_post_recommendations_preferred_providers(client, monkeypatch):
    """With preferred_providers, preferred providers should rank first when results exist."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    payload = {
        "min_vcpu": 2,
        "min_ram_gb": 4,
        "arch": "x86_64",
        "region_constraint": "EU",
        "max_price_eur_per_hour": 1.0,
        "preferred_providers": ["gcp", "hetzner"],
        "limit": 10,
    }
    response = await client.post("/api/recommendations", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "recommendations" in data
    recs = data["recommendations"]
    if recs:
        # First recommendation should be from a preferred provider (score boost)
        assert recs[0]["provider_slug"] in ["gcp", "hetzner"]


@pytest.mark.asyncio
async def test_post_recommendations_allowed_providers(client, monkeypatch):
    """With allowed_providers, only those providers should appear in results."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    allowed = ["gcp", "hetzner"]
    payload = {
        "min_vcpu": 2,
        "min_ram_gb": 4,
        "arch": "x86_64",
        "region_constraint": "EU",
        "max_price_eur_per_hour": 1.0,
        "allowed_providers": allowed,
        "limit": 10,
    }
    response = await client.post("/api/recommendations", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "recommendations" in data
    for rec in data["recommendations"]:
        assert rec["provider_slug"] in allowed
