"""E2E tests for TCO-aware recommendations.

Requires: running CloudBroker (make up), migrations, real ingested data, ingest --egress.
Skips if API is unreachable. Uses live data - no hardcoded providers/regions.
"""

import os

import pytest
import httpx

API_URL = os.environ.get("API_URL", "http://localhost:8000")
API_KEY = os.environ.get("API_KEY", "dev-bypass")
TIMEOUT = 10.0


def _api_reachable() -> bool:
    """Check if the API is reachable."""
    try:
        r = httpx.get(f"{API_URL}/health", timeout=5.0)
        return r.status_code == 200
    except Exception:
        return False


def _get_data_source() -> tuple[str, str] | None:
    """Fetch first recommendation to derive data source. Returns (provider, region) or None."""
    try:
        r = httpx.post(
            f"{API_URL}/api/recommendations",
            json={
                "min_vcpu": 2,
                "min_ram_gb": 4,
                "arch": "x86_64",
                "region_constraint": "ANY",
                "max_price_eur_per_hour": 1.0,
                "limit": 20,
            },
            headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        recs = data.get("recommendations", [])
        if not recs:
            return None
        first = recs[0]
        prov = first.get("provider_slug") or ""
        reg = first.get("region_slug") or ""
        if prov and reg:
            return (prov, reg)
        return None
    except Exception:
        return None


@pytest.fixture(scope="module")
def skip_unless_api():
    """Skip entire module if API is unreachable."""
    if not _api_reachable():
        pytest.skip("CloudBroker API unreachable (run: make up, make migrate, ingest, ingest --egress)")


@pytest.fixture(scope="module")
def data_source(skip_unless_api):
    """Derive data source from live recommendations."""
    ds = _get_data_source()
    if ds is None:
        pytest.skip("No recommendations returned - run an ingestor (e.g. make ingest-azure-quick)")
    return ds


@pytest.fixture(scope="module")
def tco_available(skip_unless_api, data_source):
    """Skip if egress_rates not loaded (TCO won't work)."""
    ds_provider, ds_region = data_source
    try:
        r = httpx.post(
            f"{API_URL}/api/recommendations",
            json={
                "min_vcpu": 2,
                "min_ram_gb": 4,
                "arch": "x86_64",
                "region_constraint": "ANY",
                "max_price_eur_per_hour": 1.0,
                "limit": 1,
                "data_source_provider": ds_provider,
                "data_source_region": ds_region,
                "estimated_egress_gb_per_hour": 1.0,
            },
            headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            recs = r.json().get("recommendations", [])
            if recs and recs[0].get("explain", {}).get("tco_eur_per_hour") is not None:
                return True
    except Exception:
        pass
    pytest.skip("egress_rates not loaded (run: make ingest-azure-egress or ingest --provider azure --egress)")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_recommendations_without_tco_has_no_tco_fields(skip_unless_api):
    """Without TCO context, explain should not have tco/egress fields (or they are null)."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{API_URL}/api/recommendations",
            json={
                "min_vcpu": 2,
                "min_ram_gb": 4,
                "arch": "x86_64",
                "region_constraint": "ANY",
                "max_price_eur_per_hour": 1.0,
                "limit": 5,
            },
            headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
            timeout=TIMEOUT,
        )
    assert r.status_code == 200
    data = r.json()
    recs = data.get("recommendations", [])
    for rec in recs:
        explain = rec.get("explain", {})
        tco = explain.get("tco_eur_per_hour")
        egress = explain.get("egress_cost_eur_per_hour")
        assert tco is None or egress is None, (
            "Without TCO context, tco/egress should be null"
        )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_recommendations_with_tco_returns_tco_fields(skip_unless_api, data_source, tco_available):
    """With TCO placement, all recommendations have tco_eur_per_hour and egress_cost_eur_per_hour."""
    ds_provider, ds_region = data_source
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{API_URL}/api/recommendations",
            json={
                "min_vcpu": 2,
                "min_ram_gb": 4,
                "arch": "x86_64",
                "region_constraint": "ANY",
                "max_price_eur_per_hour": 1.0,
                "limit": 20,
                "data_source_provider": ds_provider,
                "data_source_region": ds_region,
                "estimated_egress_gb_per_hour": 1.0,
            },
            headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
            timeout=TIMEOUT,
        )
    assert r.status_code == 200
    data = r.json()
    recs = data.get("recommendations", [])
    assert len(recs) > 0, "Expected at least one TCO recommendation"
    for rec in recs:
        explain = rec.get("explain", {})
        assert explain.get("tco_eur_per_hour") is not None
        assert explain.get("egress_cost_eur_per_hour") is not None


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_tco_same_provider_region_has_zero_egress_cost(skip_unless_api, data_source, tco_available):
    """Candidates matching data source provider+region should have egress_cost_eur_per_hour == 0."""
    ds_provider, ds_region = data_source
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{API_URL}/api/recommendations",
            json={
                "min_vcpu": 2,
                "min_ram_gb": 4,
                "arch": "x86_64",
                "region_constraint": "ANY",
                "max_price_eur_per_hour": 1.0,
                "limit": 20,
                "data_source_provider": ds_provider,
                "data_source_region": ds_region,
                "estimated_egress_gb_per_hour": 1.0,
            },
            headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
            timeout=TIMEOUT,
        )
    assert r.status_code == 200
    data = r.json()
    recs = data.get("recommendations", [])
    matching = [
        rec for rec in recs
        if rec.get("provider_slug") == ds_provider and rec.get("region_slug") == ds_region
    ]
    if not matching:
        pytest.skip("No recommendation matches data source (single-provider catalog)")
    for rec in matching:
        egress = rec.get("explain", {}).get("egress_cost_eur_per_hour")
        assert egress == 0, f"Same provider+region {ds_provider}/{ds_region} should have egress_cost=0, got {egress}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_tco_cross_provider_has_positive_egress_cost(skip_unless_api, data_source, tco_available):
    """Candidates with different provider should have egress_cost_eur_per_hour > 0 (when egress rate exists)."""
    ds_provider, ds_region = data_source
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{API_URL}/api/recommendations",
            json={
                "min_vcpu": 2,
                "min_ram_gb": 4,
                "arch": "x86_64",
                "region_constraint": "ANY",
                "max_price_eur_per_hour": 1.0,
                "limit": 20,
                "data_source_provider": ds_provider,
                "data_source_region": ds_region,
                "estimated_egress_gb_per_hour": 1.0,
            },
            headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
            timeout=TIMEOUT,
        )
    assert r.status_code == 200
    data = r.json()
    recs = data.get("recommendations", [])
    cross_provider = [
        rec for rec in recs
        if rec.get("provider_slug") != ds_provider
    ]
    if not cross_provider:
        pytest.skip("No cross-provider recommendations (single-provider catalog)")
    for rec in cross_provider:
        egress = rec.get("explain", {}).get("egress_cost_eur_per_hour")
        if egress is not None:
            assert egress > 0, (
                f"Cross-provider {rec.get('provider_slug')} should have egress_cost > 0 when rate exists, got {egress}"
            )
