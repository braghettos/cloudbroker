"""Aruba Cloud connector: config-based pricing for CloudServer flavors.

Aruba Cloud is an Italian provider with datacenters in Bergamo (Italy) and
Czech Republic. Affordable EU-focused compute with hourly billing.

API: https://api.arubacloud.com (new Cloud Management Platform, OAuth2)
    The old WsEndUser v2.9 API (api.dc{X}.computing.cloud.it) was decommissioned.
    The new API does not expose flavor listing or pricing endpoints.
Pricing: Config-based — sourced from https://www.arubacloud.com/pricing/public-cloud/
    and https://api.arubacloud.com/docs/metadata/. Update _PLANS when pricing changes.
"""

from __future__ import annotations

from app.models.provider import ProviderType
from connectors._config_connector import BaseConfigConnector, ConfigPlan, ConfigRegion


class ArubaConnector(BaseConfigConnector):
    PROVIDER_SLUG = "aruba"
    PROVIDER_NAME = "Aruba Cloud"
    PROVIDER_WEBSITE = "https://www.arubacloud.com"
    PROVIDER_TYPE = ProviderType.EU

    REGIONS = [
        ConfigRegion("itbg-dc1", "Bergamo DC1", "IT", True),
        ConfigRegion("itbg-dc2", "Bergamo DC2", "IT", True),
        ConfigRegion("itbg-dc3", "Bergamo DC3", "IT", True),
    ]

    # Sources: https://www.arubacloud.com/pricing/public-cloud/ + API metadata
    # General Purpose: (vcpu, ram_gb, disk_gb, eur_per_hour, cpu_type, family)
    _PLANS: dict[str, tuple[int, float, float, float, str, str]] = {
        # General Purpose (1:2 vCPU:RAM ratio)
        "CSO1A2": (1, 2.0, 20.0, 0.012, "shared", "general-purpose"),
        "CSO2A4": (2, 4.0, 40.0, 0.024, "shared", "general-purpose"),
        "CSO4A8": (4, 8.0, 80.0, 0.046, "shared", "general-purpose"),
        "CSO8A16": (8, 16.0, 120.0, 0.089, "shared", "general-purpose"),
        "CSO12A24": (12, 24.0, 120.0, 0.134, "shared", "general-purpose"),
        "CSO16A32": (16, 32.0, 120.0, 0.184, "shared", "general-purpose"),
        "CSO24A48": (24, 48.0, 120.0, 0.274, "shared", "general-purpose"),
        "CSO32A64": (32, 64.0, 120.0, 0.374, "shared", "general-purpose"),
        # RAM Oriented (1:4 vCPU:RAM ratio)
        "CSO1A4": (1, 4.0, 40.0, 0.018, "shared", "memory-optimized"),
        "CSO2A8": (2, 8.0, 80.0, 0.036, "shared", "memory-optimized"),
        "CSO4A16": (4, 16.0, 120.0, 0.068, "shared", "memory-optimized"),
        "CSO8A32": (8, 32.0, 120.0, 0.129, "shared", "memory-optimized"),
        "CSO16A64": (16, 64.0, 120.0, 0.254, "shared", "memory-optimized"),
    }

    @classmethod
    def get_plans(cls) -> list[ConfigPlan]:
        return [
            ConfigPlan(
                name=name,
                vcpu=vcpu,
                ram_gb=ram_gb,
                eur_per_hour=eur_per_hour,
                family=family,
                cpu_type=cpu_type,
                disk_gb=disk_gb,
                disk_type="local_ssd",
            )
            for name, (vcpu, ram_gb, disk_gb, eur_per_hour, cpu_type, family) in cls._PLANS.items()
        ]


# Module-level ingest function for CLI dispatch compatibility
async def ingest(db, only_eu=True, purchase_model="on_demand"):
    await ArubaConnector.ingest(db, only_eu, purchase_model)


# Keep for backwards compatibility with mock tests
def _build_price_map(items: list[dict]) -> dict[str, float]:
    """Build {name: eur_per_hour} from price list items (legacy helper)."""
    pm: dict[str, float] = {}
    for item in items:
        name = item.get("Name", "")
        hourly = item.get("PriceHourly")
        monthly = item.get("PriceMonthly")
        if hourly and hourly > 0:
            pm[name] = hourly
        elif monthly and monthly > 0:
            pm[name] = monthly / 730.0
    return pm


async def _get_auth_token(base_url: str, username: str, password: str) -> str | None:
    """Legacy auth function kept for test compatibility."""
    import httpx

    payload = {"ApplicationId": 1, "Username": username, "Password": password}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{base_url}/Authentication/Login",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
            if data.get("Success"):
                return data.get("Value", {}).get("Token")
            return None
    except Exception:
        return None


async def _fetch_server_types(base_url: str, token: str) -> list[dict]:
    """Legacy server types fetch kept for test compatibility."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{base_url}/Compute/GetHardwareProfiles",
                json={"Token": token},
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
            if data.get("Success"):
                return data.get("Value", [])
            return []
    except Exception:
        return []
