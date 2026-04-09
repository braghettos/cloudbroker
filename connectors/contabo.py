"""Contabo connector: config-based pricing for Cloud VPS and Cloud VDS plans.

Contabo is a German provider with exceptional price-to-performance for individual and
developer use. Monthly billing; prices sourced from https://contabo.com/en/pricing/
and converted to hourly (EUR/month ÷ 730).

API: https://api.contabo.com/ (OAuth2 client credentials, not required for ingestion)
Pricing: Config-based — update _PLANS when provider pricing changes.
"""

from __future__ import annotations

from app.models.provider import ProviderType
from connectors._config_connector import BaseConfigConnector, ConfigPlan, ConfigRegion

_HOURS_PER_MONTH = 730.0


def _monthly_to_hourly(eur_per_month: float) -> float:
    return round(eur_per_month / _HOURS_PER_MONTH, 6)


class ContaboConnector(BaseConfigConnector):
    PROVIDER_SLUG = "contabo"
    PROVIDER_NAME = "Contabo"
    PROVIDER_WEBSITE = "https://contabo.com"
    PROVIDER_TYPE = ProviderType.EU

    REGIONS = [
        ConfigRegion("eu-de-1", "Germany (Nuremberg)", "DE", True),
        ConfigRegion("eu-de-2", "Germany (Munich)", "DE", True),
        ConfigRegion("eu-gb-1", "UK (London)", "GB", False),
    ]

    # Sources: https://contabo.com/en/pricing/ — Cloud VPS and Cloud VDS sections
    _PLANS = {
        # Cloud VPS (shared vCPU, NVMe SSD)
        "cloud-vps-10": (4, 8.0, 75.0, 3.60, "shared", "general-purpose"),
        "cloud-vps-20": (6, 12.0, 100.0, 5.60, "shared", "general-purpose"),
        "cloud-vps-30": (8, 24.0, 200.0, 11.20, "shared", "general-purpose"),
        "cloud-vps-40": (12, 48.0, 250.0, 20.00, "shared", "general-purpose"),
        "cloud-vps-50": (16, 64.0, 300.0, 29.60, "shared", "general-purpose"),
        "cloud-vps-60": (18, 96.0, 350.0, 39.20, "shared", "general-purpose"),
        # Cloud VDS (dedicated AMD EPYC physical cores, NVMe SSD)
        "cloud-vds-s": (3, 24.0, 180.0, 27.52, "dedicated", "compute-optimized"),
        "cloud-vds-m": (4, 32.0, 240.0, 35.84, "dedicated", "compute-optimized"),
        "cloud-vds-l": (6, 48.0, 360.0, 51.20, "dedicated", "compute-optimized"),
    }

    @classmethod
    def get_plans(cls) -> list[ConfigPlan]:
        return [
            ConfigPlan(
                name=name,
                vcpu=vcpu,
                ram_gb=ram_gb,
                eur_per_hour=_monthly_to_hourly(eur_per_month),
                family=family,
                cpu_type=cpu_type,
                disk_gb=disk_gb,
                disk_type="local_ssd",
            )
            for name, (vcpu, ram_gb, disk_gb, eur_per_month, cpu_type, family) in cls._PLANS.items()
        ]


# Module-level ingest function for CLI dispatch compatibility
async def ingest(db, only_eu=True, purchase_model="on_demand"):
    await ContaboConnector.ingest(db, only_eu, purchase_model)
