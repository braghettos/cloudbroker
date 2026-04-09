"""Exoscale connector: config-based pricing for EU compute instances.

Exoscale is a Swiss-headquartered provider with EU datacenters in Austria, Germany,
Bulgaria, and Switzerland. Prices are uniform across all zones.

API: https://api-{zone}.exoscale.com/v2 (requires HMAC-SHA256 auth; used for specs only)
Pricing: Config-based — sourced from https://www.exoscale.com/pricing/ and
    https://calculator.exoscale.com/. Update _PLANS when pricing changes.

Note: Exoscale migrated from zone-as-query-param to zone-in-hostname in API v2.
    The pricing API was removed from public responses. We use config-based pricing
    to avoid auth dependency for the most common use case.
"""

from __future__ import annotations

from app.models.provider import ProviderType
from connectors._config_connector import BaseConfigConnector, ConfigPlan, ConfigRegion


class ExoscaleConnector(BaseConfigConnector):
    PROVIDER_SLUG = "exoscale"
    PROVIDER_NAME = "Exoscale"
    PROVIDER_WEBSITE = "https://www.exoscale.com"
    PROVIDER_TYPE = ProviderType.EU

    REGIONS = [
        ConfigRegion("ch-gva-2", "Geneva", "CH", False),
        ConfigRegion("at-vie-1", "Vienna 1", "AT", True),
        ConfigRegion("at-vie-2", "Vienna 2", "AT", True),
        ConfigRegion("de-muc-1", "Munich", "DE", True),
        ConfigRegion("bg-sof-1", "Sofia", "BG", True),
    ]

    # Sources: https://www.exoscale.com/pricing/ — Compute Instances (EUR/hr, uniform across zones)
    # (name, vcpu, ram_gb, eur_per_hour, family, cpu_type)
    _PLANS: dict[str, tuple[int, float, float, str, str]] = {
        # Standard (General Purpose)
        "standard.micro":       (1,   0.5,  0.007, "general-purpose",   "dedicated"),
        "standard.tiny":        (1,   1.0,  0.015, "general-purpose",   "dedicated"),
        "standard.small":       (2,   2.0,  0.023, "general-purpose",   "dedicated"),
        "standard.medium":      (2,   4.0,  0.047, "general-purpose",   "dedicated"),
        "standard.large":       (4,   8.0,  0.093, "general-purpose",   "dedicated"),
        "standard.extra-large": (4,  16.0,  0.187, "general-purpose",   "dedicated"),
        "standard.huge":        (8,  32.0,  0.373, "general-purpose",   "dedicated"),
        "standard.mega":        (12, 64.0,  0.747, "general-purpose",   "dedicated"),
        "standard.titan":       (16, 128.0, 1.493, "general-purpose",   "dedicated"),
        "standard.jumbo":       (24, 225.0, 2.240, "general-purpose",   "dedicated"),
        # CPU-Optimized
        "cpu.extra-large":      (8,  16.0,  0.230, "compute-optimized", "dedicated"),
        "cpu.huge":             (16, 32.0,  0.470, "compute-optimized", "dedicated"),
        "cpu.mega":             (32, 64.0,  0.930, "compute-optimized", "dedicated"),
        "cpu.titan":            (40, 128.0, 1.870, "compute-optimized", "dedicated"),
        # Memory-Optimized
        "memory.extra-large":   (2,  16.0,  0.140, "memory-optimized",  "dedicated"),
        "memory.huge":          (4,  32.0,  0.280, "memory-optimized",  "dedicated"),
        "memory.mega":          (8,  64.0,  0.560, "memory-optimized",  "dedicated"),
        "memory.titan":         (12, 128.0, 1.120, "memory-optimized",  "dedicated"),
        # Storage-Optimized
        "storage.extra-large":  (4,  16.0,  0.140, "storage-optimized", "dedicated"),
        "storage.huge":         (8,  32.0,  0.280, "storage-optimized", "dedicated"),
        "storage.mega":         (12, 64.0,  0.560, "storage-optimized", "dedicated"),
        "storage.titan":        (16, 128.0, 1.120, "storage-optimized", "dedicated"),
        "storage.jumbo":        (24, 225.0, 1.870, "storage-optimized", "dedicated"),
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
            )
            for name, (vcpu, ram_gb, eur_per_hour, family, cpu_type) in cls._PLANS.items()
        ]


# Module-level ingest function for CLI dispatch compatibility
async def ingest(db, only_eu=True, purchase_model="on_demand"):
    await ExoscaleConnector.ingest(db, only_eu, purchase_model)


# Keep _parse_family for backwards compatibility with tests
def _parse_family(name: str) -> str:
    """Infer instance family from Exoscale instance type name using common taxonomy."""
    n = name.lower()
    if n.startswith("gpu"):
        return "gpu"
    if n.startswith("cpu"):
        return "compute-optimized"
    if n.startswith("memory"):
        return "memory-optimized"
    if n.startswith("storage"):
        return "storage-optimized"
    return "general-purpose"
