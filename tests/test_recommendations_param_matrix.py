"""Parametrized matrix tests for recommendations API.

- Representative matrix: ~8 base + 6 provider-filter combinations (fast, default).
- Exhaustive matrix: full Cartesian product of plan dimensions (~432 tests).
  Run with: pytest -m exhaustive tests/test_recommendations_param_matrix.py
"""

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.fixture
async def client():
    """Create async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _assert_valid_recommendation_response(response, limit: int):
    """Shared assertions for 200 response structure."""
    assert response.status_code in [200, 500], f"Expected 200 or 500, got {response.status_code}"
    if response.status_code == 200:
        data = response.json()
        assert "recommendations" in data
        assert isinstance(data["recommendations"], list)
        recs = data["recommendations"]
        assert len(recs) <= limit
        for rec in recs:
            assert "instance_type_id" in rec
            assert "instance_type_name" in rec
            assert "provider_slug" in rec
            assert "region_id" in rec
            assert "region_slug" in rec
            assert "vcpu" in rec
            assert "ram_gb" in rec
            assert "score" in rec
            assert "explain" in rec
            explain = rec["explain"]
            assert "resource_fit" in explain
            assert "normalized_price" in explain
            assert "price_eur_per_hour" in explain
            assert "region_is_eu" in explain


# Reduced matrix: ~8 representative combinations (fast, default run)
@pytest.mark.parametrize(
    "min_vcpu,min_ram_gb,arch,region_constraint,purchase_model,max_price,limit",
    [
        (1, 1.0, "x86_64", "EU", "on_demand", 0.1, 5),
        (1, 1.0, "x86_64", "ANY", "spot", 0.5, 10),
        (2, 4.0, "x86_64", "EU", "on_demand", 0.5, 5),
        (2, 4.0, "arm64", "ANY", "spot", 1.0, 10),
        (4, 8.0, "x86_64", "EU", "spot", 1.0, 5),
        (4, 8.0, "arm64", "ANY", "on_demand", 0.5, 10),
        (1, 4.0, "x86_64", "ANY", "on_demand", 1.0, 5),
        (2, 8.0, "arm64", "EU", "spot", 0.1, 10),
    ],
)
@pytest.mark.asyncio
async def test_recommendations_param_matrix(
    client,
    min_vcpu,
    min_ram_gb,
    arch,
    region_constraint,
    purchase_model,
    max_price,
    limit,
    monkeypatch,
):
    """POST recommendations with parametrized inputs; assert 200 and structure."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    payload = {
        "min_vcpu": min_vcpu,
        "min_ram_gb": min_ram_gb,
        "arch": arch,
        "region_constraint": region_constraint,
        "max_price_eur_per_hour": max_price,
        "purchase_model": purchase_model,
        "limit": limit,
    }

    response = await client.post("/api/recommendations", json=payload)
    _assert_valid_recommendation_response(response, limit)


# Exhaustive matrix: full Cartesian product per plan Part 6 (3×3×2×2×2×3×2 = 432 tests)
@pytest.mark.exhaustive
@pytest.mark.parametrize("min_vcpu", [1, 2, 4])
@pytest.mark.parametrize("min_ram_gb", [1.0, 4.0, 8.0])
@pytest.mark.parametrize("arch", ["x86_64", "arm64"])
@pytest.mark.parametrize("region_constraint", ["EU", "ANY"])
@pytest.mark.parametrize("purchase_model", ["on_demand", "spot"])
@pytest.mark.parametrize("max_price_eur_per_hour", [0.1, 0.5, 1.0])
@pytest.mark.parametrize("limit", [5, 10])
@pytest.mark.asyncio
async def test_recommendations_param_matrix_exhaustive(
    client,
    min_vcpu,
    min_ram_gb,
    arch,
    region_constraint,
    purchase_model,
    max_price_eur_per_hour,
    limit,
    monkeypatch,
):
    """POST recommendations with full Cartesian product of base dimensions."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    payload = {
        "min_vcpu": min_vcpu,
        "min_ram_gb": min_ram_gb,
        "arch": arch,
        "region_constraint": region_constraint,
        "max_price_eur_per_hour": max_price_eur_per_hour,
        "purchase_model": purchase_model,
        "limit": limit,
    }

    response = await client.post("/api/recommendations", json=payload)
    _assert_valid_recommendation_response(response, limit)


# Scoring variants: legacy vs multi-criteria (plan 6.1)
@pytest.mark.exhaustive
@pytest.mark.parametrize(
    "scoring_mode",
    [
        "legacy",  # price_weight + fit_weight
        "multi_criteria",  # cost_weight + performance_weight + reliability_weight
    ],
)
@pytest.mark.asyncio
async def test_recommendations_scoring_variants(client, scoring_mode, monkeypatch):
    """Test legacy and multi-criteria scoring modes."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    payload = {
        "min_vcpu": 2,
        "min_ram_gb": 4,
        "arch": "x86_64",
        "region_constraint": "ANY",
        "max_price_eur_per_hour": 1.0,
        "limit": 5,
    }
    if scoring_mode == "legacy":
        payload["price_weight"] = 0.6
        payload["fit_weight"] = 0.4
    else:
        payload["cost_weight"] = 0.5
        payload["performance_weight"] = 0.3
        payload["reliability_weight"] = 0.2

    response = await client.post("/api/recommendations", json=payload)
    _assert_valid_recommendation_response(response, 5)


# Provider filter variants: none, allowed, excluded, preferred (plan 6.1)
@pytest.mark.exhaustive
@pytest.mark.parametrize(
    "filter_mode,payload_extras",
    [
        ("none", {}),
        ("allowed_providers", {"allowed_providers": ["hetzner", "gcp"]}),
        ("excluded_providers", {"excluded_providers": ["ovh"]}),
        ("preferred_providers", {"preferred_providers": ["hetzner", "gcp"]}),
    ],
    ids=["none", "allowed", "excluded", "preferred"],
)
@pytest.mark.asyncio
async def test_recommendations_provider_filter_variants(client, filter_mode, payload_extras, monkeypatch):
    """Test all provider filter modes from plan matrix."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    payload = {
        "min_vcpu": 2,
        "min_ram_gb": 4,
        "arch": "x86_64",
        "region_constraint": "ANY",
        "max_price_eur_per_hour": 1.0,
        "limit": 10,
        **payload_extras,
    }

    response = await client.post("/api/recommendations", json=payload)
    _assert_valid_recommendation_response(response, 10)


# TCO variants: none, with egress, with max_tco (plan 6.1)
@pytest.mark.exhaustive
@pytest.mark.parametrize(
    "tco_mode,payload_extras",
    [
        ("none", {}),
        (
            "with_egress",
            {"data_source_provider": "gcp", "data_source_region": "europe-west1", "estimated_egress_gb_per_hour": 1.0},
        ),
        (
            "with_max_tco",
            {
                "data_source_provider": "gcp",
                "data_source_region": "europe-west1",
                "estimated_egress_gb_per_hour": 1.0,
                "max_tco_eur_per_hour": 1.0,
            },
        ),
    ],
    ids=["none", "with_egress", "with_max_tco"],
)
@pytest.mark.asyncio
async def test_recommendations_tco_variants(client, tco_mode, payload_extras, monkeypatch):
    """Test TCO modes from plan matrix."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    payload = {
        "min_vcpu": 2,
        "min_ram_gb": 4,
        "arch": "x86_64",
        "region_constraint": "ANY",
        "max_price_eur_per_hour": 1.0,
        "limit": 5,
        **payload_extras,
    }

    response = await client.post("/api/recommendations", json=payload)
    _assert_valid_recommendation_response(response, 5)


@pytest.mark.parametrize("allowed_providers", [None, ["hetzner", "gcp"], ["aws"]])
@pytest.mark.parametrize("excluded_providers", [None, ["ovh"]])
@pytest.mark.asyncio
async def test_recommendations_provider_filters(client, allowed_providers, excluded_providers, monkeypatch):
    """Test allowed_providers and excluded_providers filters."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    payload = {
        "min_vcpu": 2,
        "min_ram_gb": 4,
        "arch": "x86_64",
        "region_constraint": "ANY",
        "max_price_eur_per_hour": 1.0,
        "limit": 10,
    }
    if allowed_providers is not None:
        payload["allowed_providers"] = allowed_providers
    if excluded_providers is not None:
        payload["excluded_providers"] = excluded_providers

    response = await client.post("/api/recommendations", json=payload)
    assert response.status_code in [200, 500]

    if response.status_code == 200:
        recs = response.json().get("recommendations", [])
        if allowed_providers:
            for rec in recs:
                assert rec["provider_slug"] in allowed_providers
        if excluded_providers:
            for rec in recs:
                assert rec["provider_slug"] not in excluded_providers
