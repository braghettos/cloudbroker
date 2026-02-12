"""Pydantic schemas for recommendation request and response."""

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Arch(str, Enum):
    """Supported architecture for recommendations (canonical values)."""

    X86_64 = "x86_64"
    ARM64 = "arm64"


def normalize_arch(arch: str) -> str:
    """
    Normalize Kubernetes architecture values to CloudBroker canonical values.

    Maps:
    - amd64, x86_64 -> x86_64
    - arm64, aarch64 -> arm64

    Raises ValueError if architecture is not recognized.
    """
    arch_lower = arch.lower()
    if arch_lower in ("amd64", "x86_64"):
        return "x86_64"
    elif arch_lower in ("arm64", "aarch64"):
        return "arm64"
    else:
        raise ValueError(
            f"Unsupported architecture: {arch}. "
            f"Supported values: amd64, x86_64, arm64, aarch64"
        )


class RegionConstraint(str, Enum):
    """Region constraint for recommendations."""

    EU = "EU"
    ANY = "ANY"


class RecommendationRequest(BaseModel):
    """Input for POST /recommendations."""

    min_vcpu: int = Field(..., ge=1, description="Minimum vCPUs required")
    min_ram_gb: float = Field(..., gt=0, description="Minimum RAM in GB required (int or float)")
    arch: str = Field(
        ...,
        description="Architecture requirement. Accepts Kubernetes values (amd64, x86_64, arm64, aarch64) and normalizes to canonical values (x86_64, arm64)."
    )

    @field_validator("arch")
    @classmethod
    def normalize_arch(cls, v: str) -> str:
        """Normalize architecture value to canonical form."""
        return normalize_arch(v)
    region_constraint: RegionConstraint = Field(
        default=RegionConstraint.ANY,
        description="Restrict to EU regions when EU",
    )
    max_price_eur_per_hour: float = Field(..., ge=0, description="Maximum price per hour in EUR")
    purchase_model: str = Field(
        default="on_demand",
        description="Purchase model: 'on_demand' or 'spot'",
    )
    limit: int = Field(default=10, ge=1, le=100, description="Max number of recommendations to return")
    price_weight: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional override for price weight in scoring (0-1). If both price_weight and fit_weight are provided, they will be normalized.",
    )
    fit_weight: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional override for resource-fit weight in scoring (0-1). If both price_weight and fit_weight are provided, they will be normalized.",
    )
    preferred_providers: list[str] | None = Field(
        default=None,
        description="Optional list of provider slugs to prefer (e.g. ['hetzner', 'gcp']). Preferred providers receive a score multiplier.",
    )
    allowed_providers: list[str] | None = Field(
        default=None,
        description="Optional list of provider slugs to restrict recommendations to (e.g. ['hetzner', 'gcp', 'scaleway']). When set, only offers from these providers are returned. Ignored if empty.",
    )
    excluded_providers: list[str] | None = Field(
        default=None,
        description="Optional list of provider slugs to exclude from recommendations.",
    )
    cost_weight: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional override for cost weight in multi-criteria scoring (0-1). If cost/performance/reliability weights are provided, they will be normalized.",
    )
    performance_weight: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional override for performance weight in multi-criteria scoring (0-1).",
    )
    reliability_weight: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional override for reliability weight in multi-criteria scoring (0-1).",
    )


class RecommendationExplain(BaseModel):
    """Per-recommendation explanation of the score."""

    resource_fit: float = Field(..., description="Fit of instance resources vs request (0-1, higher = better match)")
    normalized_price: float = Field(
        ...,
        description="Price score component (0-1, higher = cheaper relative to result set)",
    )
    price_weight: float = Field(..., description="Weight applied to price in score (legacy, for backward compatibility)")
    fit_weight: float = Field(..., description="Weight applied to resource_fit in score (legacy, for backward compatibility)")
    performance_score: float | None = Field(
        default=None,
        description="Performance score component (0-1, higher = better performance relative to result set)",
    )
    reliability_score: float | None = Field(
        default=None,
        description="Reliability score component (0-1, higher = more reliable provider)",
    )
    cost_weight: float | None = Field(default=None, description="Weight applied to cost in multi-criteria score")
    performance_weight: float | None = Field(default=None, description="Weight applied to performance in multi-criteria score")
    reliability_weight: float | None = Field(default=None, description="Weight applied to reliability in multi-criteria score")
    spot_availability_score: float | None = Field(
        default=None,
        description="Spot availability score (0-1, higher = more likely available as spot). Only present when purchase_model=spot.",
    )
    interruption_risk_score: float | None = Field(
        default=None,
        description="Interruption risk score (0-1, higher = higher risk of interruption). Only present when purchase_model=spot.",
    )
    price_eur_per_hour: float = Field(..., description="Price used (EUR/h)")
    region_is_eu: bool = Field(..., description="Whether the region is EU")


class RecommendationItem(BaseModel):
    """A single recommendation with score and explain."""

    instance_type_id: int
    instance_type_name: str
    provider_slug: str
    region_id: int
    region_slug: str
    vcpu: int
    ram_gb: float
    arch: str | None
    score: float = Field(..., description="Composite score (higher = better)")
    explain: RecommendationExplain


class RecommendationResponse(BaseModel):
    """Response for POST /recommendations."""

    recommendations: list[RecommendationItem]
