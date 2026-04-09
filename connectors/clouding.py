"""Clouding.io connector: config-based pricing for Spanish cloud provider.

Clouding.io is a Barcelona-based provider operating from a Tier IV data center.
Fills the Iberian Peninsula gap in EU coverage. Ultra-affordable hourly billing
with no minimum commitments.

API: https://api.clouding.io (REST API available; pricing from public pricing page)
Pricing: Config-based — update _PLANS when provider pricing changes.
Sources: https://clouding.io/en/ and https://www.vpsbenchmarks.com/hosters/clouding_io
"""

from __future__ import annotations

from app.models.provider import ProviderType
from connectors._config_connector import BaseConfigConnector, ConfigPlan, ConfigRegion


class CloudingConnector(BaseConfigConnector):
    PROVIDER_SLUG = "clouding"
    PROVIDER_NAME = "Clouding.io"
    PROVIDER_WEBSITE = "https://clouding.io"
    PROVIDER_TYPE = ProviderType.EU

    REGIONS = [
        ConfigRegion("es-bcn-1", "Barcelona (Tier IV)", "ES", True),
    ]

    # (vcpu, ram_gb, disk_gb, eur_per_hour)
    # Sources: https://clouding.io/en/pricing/ — standard flexible plans
    _PLANS = {
        "micro-1": (1, 1.0, 5.0, 0.004),
        "micro-2": (1, 2.0, 30.0, 0.011),
        "small-1": (2, 4.0, 60.0, 0.022),
        "small-2": (4, 8.0, 100.0, 0.045),
        "medium-1": (6, 16.0, 200.0, 0.090),
        "medium-2": (8, 32.0, 400.0, 0.180),
        "large-1": (12, 48.0, 600.0, 0.270),
        "large-2": (16, 64.0, 800.0, 0.360),
    }

    @classmethod
    def get_plans(cls) -> list[ConfigPlan]:
        return [
            ConfigPlan(
                name=name,
                vcpu=vcpu,
                ram_gb=ram_gb,
                eur_per_hour=eur_per_hour,
                family="general-purpose",
                cpu_type="shared",
                disk_gb=disk_gb,
                disk_type="local_ssd",
            )
            for name, (vcpu, ram_gb, disk_gb, eur_per_hour) in cls._PLANS.items()
        ]


async def ingest(db, only_eu=True, purchase_model="on_demand"):
    await CloudingConnector.ingest(db, only_eu, purchase_model)
