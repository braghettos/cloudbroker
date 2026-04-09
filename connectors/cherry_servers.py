"""Cherry Servers connector: config-based pricing for virtual server plans.

Cherry Servers is a Lithuanian provider covering the Baltic region (gap in current
EU coverage). Plans and pricing sourced from https://www.cherryservers.com/pricing.

API: https://api.cherryservers.com/v1/ (Bearer token; not required for pricing ingestion)
Pricing: Config-based — update _PLANS when provider pricing changes.
"""

from __future__ import annotations

from app.models.provider import ProviderType
from connectors._config_connector import BaseConfigConnector, ConfigPlan, ConfigRegion


class CherryServersConnector(BaseConfigConnector):
    PROVIDER_SLUG = "cherry_servers"
    PROVIDER_NAME = "Cherry Servers"
    PROVIDER_WEBSITE = "https://www.cherryservers.com"
    PROVIDER_TYPE = ProviderType.EU

    REGIONS = [
        ConfigRegion("eu-lt-1", "Šiauliai (Lithuania)", "LT", True),
        ConfigRegion("eu-nl-1", "Amsterdam (Netherlands)", "NL", True),
        ConfigRegion("eu-de-1", "Frankfurt (Germany)", "DE", True),
        ConfigRegion("eu-se-1", "Stockholm (Sweden)", "SE", True),
    ]

    # (vcpu, ram_gb, disk_gb, eur_per_hour)
    # Sources: https://www.cherryservers.com/pricing — Virtual Servers section
    _PLANS = {
        "virtual-ssd-1": (1, 1.0, 20.0, 0.015),
        "virtual-ssd-2": (2, 2.0, 40.0, 0.025),
        "virtual-ssd-4": (4, 4.0, 80.0, 0.047),
        "virtual-ssd-8": (8, 8.0, 160.0, 0.094),
        "virtual-ssd-16": (16, 16.0, 320.0, 0.188),
        "virtual-ssd-32": (32, 32.0, 640.0, 0.376),
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
                cpu_type="dedicated",
                disk_gb=disk_gb,
                disk_type="local_ssd",
            )
            for name, (vcpu, ram_gb, disk_gb, eur_per_hour) in cls._PLANS.items()
        ]


async def ingest(db, only_eu=True, purchase_model="on_demand"):
    await CherryServersConnector.ingest(db, only_eu, purchase_model)
