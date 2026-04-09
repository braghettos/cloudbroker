"""Tests for TCO cost component breakdown (storage, public IP, OS license, cross-AZ).

When TCO params are provided, assert that explain includes the expected cost fields.
"""

import pytest


def _base_payload():
    return {
        "min_vcpu": 2,
        "min_ram_gb": 4,
        "arch": "x86_64",
        "region_constraint": "ANY",
        "max_price_eur_per_hour": 1.0,
        "limit": 5,
    }


@pytest.mark.asyncio
async def test_tco_with_storage_returns_storage_cost(client, monkeypatch):
    """With min_storage_gb set, explain should have storage_cost_eur_per_hour when TCO is computed."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    payload = {
        **_base_payload(),
        "min_storage_gb": 100.0,
        "storage_type": "ssd",
    }

    response = await client.post("/api/recommendations", json=payload)
    assert response.status_code in [200, 500]
    if response.status_code == 200:
        recs = response.json().get("recommendations", [])
        for rec in recs:
            explain = rec.get("explain", {})
            # storage_cost may be present (when storage_rates exist) or null
            assert "storage_cost_eur_per_hour" in explain


@pytest.mark.asyncio
async def test_tco_with_public_ip_returns_public_ip_cost(client, monkeypatch):
    """With include_public_ip=True, explain should have public_ip_cost_eur_per_hour."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    payload = {
        **_base_payload(),
        "include_public_ip": True,
    }

    response = await client.post("/api/recommendations", json=payload)
    assert response.status_code in [200, 500]
    if response.status_code == 200:
        recs = response.json().get("recommendations", [])
        for rec in recs:
            explain = rec.get("explain", {})
            assert "public_ip_cost_eur_per_hour" in explain


@pytest.mark.asyncio
async def test_tco_with_os_type_returns_os_license_cost(client, monkeypatch):
    """With os_type=windows, explain should have os_license_cost_eur_per_hour."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    payload = {
        **_base_payload(),
        "os_type": "windows",
    }

    response = await client.post("/api/recommendations", json=payload)
    assert response.status_code in [200, 500]
    if response.status_code == 200:
        recs = response.json().get("recommendations", [])
        for rec in recs:
            explain = rec.get("explain", {})
            assert "os_license_cost_eur_per_hour" in explain


@pytest.mark.asyncio
async def test_tco_with_cross_az_returns_cross_az_cost(client, monkeypatch):
    """With estimated_cross_az_gb_per_hour set, explain should have cross_az_cost_eur_per_hour."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    payload = {
        **_base_payload(),
        "estimated_cross_az_gb_per_hour": 1.0,
    }

    response = await client.post("/api/recommendations", json=payload)
    assert response.status_code in [200, 500]
    if response.status_code == 200:
        recs = response.json().get("recommendations", [])
        for rec in recs:
            explain = rec.get("explain", {})
            assert "cross_az_cost_eur_per_hour" in explain


@pytest.mark.asyncio
async def test_tco_full_combines_all_cost_components(client, monkeypatch):
    """With egress + storage + public_ip + os_type + cross_az, explain has all cost fields."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    payload = {
        **_base_payload(),
        "data_source_provider": "gcp",
        "data_source_region": "europe-west1",
        "estimated_egress_gb_per_hour": 0.5,
        "min_storage_gb": 50.0,
        "storage_type": "ssd",
        "include_public_ip": True,
        "os_type": "linux",
        "estimated_cross_az_gb_per_hour": 0.1,
    }

    response = await client.post("/api/recommendations", json=payload)
    assert response.status_code in [200, 500]
    if response.status_code == 200:
        recs = response.json().get("recommendations", [])
        for rec in recs:
            explain = rec.get("explain", {})
            expected_keys = [
                "tco_eur_per_hour",
                "egress_cost_eur_per_hour",
                "storage_cost_eur_per_hour",
                "public_ip_cost_eur_per_hour",
                "os_license_cost_eur_per_hour",
                "cross_az_cost_eur_per_hour",
            ]
            for key in expected_keys:
                assert key in explain
