"""Gcore Cloud connector: per-resource config-based pricing for EU regions.

Gcore is a Luxembourg-headquartered provider with 9+ European locations and competitive
pricing. Uses per-minute billing; rates sourced from https://gcore.com/pricing/cloud.

API: https://api.gcore.com/cloud (public pricing endpoints available)
Pricing: Config-based per-resource model — update rates when provider pricing changes.
"""

from __future__ import annotations

from app.models.provider import ProviderType
from connectors._config_connector import BaseConfigConnector, ConfigPlan, ConfigRegion

# Per-resource rates in EUR/hr sourced from https://gcore.com/pricing/cloud
_EUR_PER_VCPU_HR: float = 0.0066
_EUR_PER_GB_RAM_HR: float = 0.0033

# Standard vCPU/RAM combinations — Gcore bills per-resource per minute
_STANDARD_CONFIGS: list[tuple[int, float]] = [
    (1, 2), (1, 4),
    (2, 4), (2, 8),
    (4, 8), (4, 16),
    (8, 16), (8, 32),
    (16, 32), (16, 64),
]


class GcoreConnector(BaseConfigConnector):
    PROVIDER_SLUG = "gcore"
    PROVIDER_NAME = "Gcore"
    PROVIDER_WEBSITE = "https://gcore.com/cloud"
    PROVIDER_TYPE = ProviderType.EU

    REGIONS = [
        ConfigRegion("lux-1", "Luxembourg 1", "LU", True),
        ConfigRegion("lux-2", "Luxembourg 2", "LU", True),
        ConfigRegion("ams-1", "Amsterdam", "NL", True),
        ConfigRegion("fra-1", "Frankfurt", "DE", True),
        ConfigRegion("darm-1", "Darmstadt", "DE", True),
        ConfigRegion("par-2", "Paris", "FR", True),
        ConfigRegion("waw-1", "Warsaw", "PL", True),
        ConfigRegion("hel-1", "Helsinki", "FI", True),
        ConfigRegion("lon-1", "London", "GB", False),
    ]

    @classmethod
    def get_plans(cls) -> list[ConfigPlan]:
        return [
            ConfigPlan(
                name=f"g1-standard-{vcpu}-{int(ram_gb)}",
                vcpu=vcpu,
                ram_gb=float(ram_gb),
                eur_per_hour=round(vcpu * _EUR_PER_VCPU_HR + ram_gb * _EUR_PER_GB_RAM_HR, 6),
                family="general-purpose",
                cpu_type="dedicated",
            )
            for vcpu, ram_gb in _STANDARD_CONFIGS
        ]


async def ingest(db, only_eu=True, purchase_model="on_demand"):
    await GcoreConnector.ingest(db, only_eu, purchase_model)
