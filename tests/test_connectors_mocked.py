"""Mocked connector tests using respx for HTTP mocking (no real API calls)."""

import pytest
import respx
import httpx

from connectors._http import fetch_with_retry


@pytest.mark.asyncio
class TestFetchWithRetry:
    """Tests for the shared fetch_with_retry utility."""

    @respx.mock
    async def test_success_on_first_attempt(self):
        route = respx.get("https://api.example.com/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        async with httpx.AsyncClient() as client:
            resp = await fetch_with_retry(client, "GET", "https://api.example.com/data", caller="test")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert route.call_count == 1

    @respx.mock
    async def test_retries_on_503(self):
        route = respx.get("https://api.example.com/data").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        async with httpx.AsyncClient() as client:
            resp = await fetch_with_retry(
                client, "GET", "https://api.example.com/data",
                retries=3, backoff=0.01, caller="test",
            )
        assert resp.status_code == 200
        assert route.call_count == 2

    @respx.mock
    async def test_retries_on_429(self):
        route = respx.get("https://api.example.com/data").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0.01"}),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        async with httpx.AsyncClient() as client:
            resp = await fetch_with_retry(
                client, "GET", "https://api.example.com/data",
                retries=3, backoff=0.01, caller="test",
            )
        assert resp.status_code == 200
        assert route.call_count == 2

    @respx.mock
    async def test_raises_after_exhausting_retries(self):
        respx.get("https://api.example.com/data").mock(
            side_effect=httpx.ConnectTimeout("connection timed out")
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.ConnectTimeout):
                await fetch_with_retry(
                    client, "GET", "https://api.example.com/data",
                    retries=2, backoff=0.01, caller="test",
                )

    @respx.mock
    async def test_no_retry_on_4xx(self):
        route = respx.get("https://api.example.com/data").mock(
            return_value=httpx.Response(404)
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.HTTPStatusError):
                await fetch_with_retry(
                    client, "GET", "https://api.example.com/data",
                    retries=3, backoff=0.01, caller="test",
                )
        assert route.call_count == 1


@pytest.mark.asyncio
class TestUpcloudConnectorMocked:
    """Mocked tests for UpCloud connector parsing logic."""

    @respx.mock
    async def test_fetch_plans_parses_response(self):
        from connectors.upcloud import _fetch_plans

        respx.get("https://api.upcloud.com/1.2/plan").mock(
            return_value=httpx.Response(200, json={
                "plans": {
                    "plan": [
                        {"name": "1xCPU-1GB", "core_number": 1, "memory_amount": 1024},
                        {"name": "2xCPU-4GB", "core_number": 2, "memory_amount": 4096},
                    ]
                }
            })
        )
        plans = await _fetch_plans({"Authorization": "Bearer test"})
        assert len(plans) == 2
        assert plans[0]["name"] == "1xCPU-1GB"
        assert plans[1]["core_number"] == 2

    @respx.mock
    async def test_fetch_zones_parses_response(self):
        from connectors.upcloud import _fetch_zones

        respx.get("https://api.upcloud.com/1.2/zone").mock(
            return_value=httpx.Response(200, json={
                "zones": {
                    "zone": [
                        {"id": "fi-hel1", "description": "Helsinki #1"},
                        {"id": "de-fra1", "description": "Frankfurt #1"},
                    ]
                }
            })
        )
        zones = await _fetch_zones({"Authorization": "Bearer test"})
        assert len(zones) == 2
        assert zones[0]["id"] == "fi-hel1"


@pytest.mark.asyncio
class TestExoscaleConnectorMocked:
    """Mocked tests for Exoscale connector."""

    @respx.mock
    async def test_fetch_instance_types_parses_response(self, monkeypatch):
        from connectors import exoscale

        monkeypatch.setattr(exoscale.settings, "exoscale_api_key", "test-key")
        monkeypatch.setattr(exoscale.settings, "exoscale_api_secret", "test-secret")

        respx.get("https://api.exoscale.com/v2/instance-type").mock(
            return_value=httpx.Response(200, json={
                "instance-types": [
                    {"id": "b612021a-c0a1-4874-bc4f-2bc19169fcd3", "name": "standard.large",
                     "cpus": 4, "memory": 8589934592, "disk": 0, "unit-price": 0.048},
                    {"id": "c2b12bb4-78d0-4e6d-b98e-cfb7baad5c01", "name": "cpu.extra-large",
                     "cpus": 8, "memory": 17179869184, "disk": 0, "unit-price": 0.096},
                ]
            })
        )
        types = await exoscale._fetch_instance_types("at-vie-1")
        assert len(types) == 2
        assert types[0]["name"] == "standard.large"
        assert types[0]["cpus"] == 4

    def test_parse_family(self):
        from connectors.exoscale import _parse_family
        assert _parse_family("standard-large") == "standard"
        assert _parse_family("cpu-huge") == "cpu"
        assert _parse_family("memory-mega") == "memory"
        assert _parse_family("gpu-small") == "gpu"

    @respx.mock
    async def test_missing_credentials_returns_early(self, monkeypatch):
        from connectors import exoscale
        monkeypatch.setattr(exoscale.settings, "exoscale_api_key", None)
        monkeypatch.setattr(exoscale.settings, "exoscale_api_secret", None)

        with pytest.raises(RuntimeError, match="EXOSCALE_API_KEY"):
            await exoscale._fetch_instance_types("at-vie-1")



@pytest.mark.asyncio
class TestArubaMocked:
    """Mocked tests for Aruba Cloud connector."""

    @respx.mock
    async def test_get_auth_token_success(self):
        from connectors.aruba import _get_auth_token

        respx.post("https://api.dc1.computing.cloud.it/WsEndUser/v2.9/WsEndUser.svc/json/Authentication/Login").mock(
            return_value=httpx.Response(200, json={
                "Success": True,
                "Value": {"Token": "test-token-abc"},
            })
        )
        token = await _get_auth_token(
            "https://api.dc1.computing.cloud.it/WsEndUser/v2.9/WsEndUser.svc/json",
            "user", "pass"
        )
        assert token == "test-token-abc"

    @respx.mock
    async def test_get_auth_token_failure_returns_none(self):
        from connectors.aruba import _get_auth_token

        respx.post("https://api.dc1.computing.cloud.it/WsEndUser/v2.9/WsEndUser.svc/json/Authentication/Login").mock(
            return_value=httpx.Response(200, json={"Success": False, "Message": "Invalid credentials"})
        )
        token = await _get_auth_token(
            "https://api.dc1.computing.cloud.it/WsEndUser/v2.9/WsEndUser.svc/json",
            "bad-user", "bad-pass"
        )
        assert token is None

    @respx.mock
    async def test_fetch_server_types_parses_flavors(self):
        from connectors.aruba import _fetch_server_types

        respx.post("https://api.dc1.computing.cloud.it/WsEndUser/v2.9/WsEndUser.svc/json/Compute/GetHardwareProfiles").mock(
            return_value=httpx.Response(200, json={
                "Success": True,
                "Value": [
                    {"Name": "CSO2A4", "CpuCount": 2, "RamCount": 4096, "DiskCount": 40},
                    {"Name": "CSO4A8", "CpuCount": 4, "RamCount": 8192, "DiskCount": 80},
                ]
            })
        )
        types = await _fetch_server_types(
            "https://api.dc1.computing.cloud.it/WsEndUser/v2.9/WsEndUser.svc/json",
            "test-token"
        )
        assert len(types) == 2
        assert types[0]["Name"] == "CSO2A4"
        assert types[0]["CpuCount"] == 2

    def test_build_price_map(self):
        from connectors.aruba import _build_price_map
        items = [
            {"Name": "CSO2A4", "PriceMonthly": 18.98},
            {"Name": "CSO4A8", "PriceHourly": 0.052},
        ]
        pm = _build_price_map(items)
        assert "CSO4A8" in pm
        assert pm["CSO4A8"] == pytest.approx(0.052, abs=1e-4)
        assert "CSO2A4" in pm
        assert pm["CSO2A4"] == pytest.approx(18.98 / 730.0, abs=1e-4)
