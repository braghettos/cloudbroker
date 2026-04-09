"""Scoring functions for recommendation candidates.

Standalone functions that compute individual scoring dimensions:
reliability, performance, spot availability, interruption risk, sustainability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.models import ProviderType

if TYPE_CHECKING:
    from app.services.candidate_fetcher import Candidate


# Small score penalty when only USD price is available (no EUR)
USD_ONLY_PENALTY = 0.95


def compute_reliability_score(provider_type: ProviderType) -> float:
    """Compute reliability score based on provider type.

    HYPERSCALER providers (AWS, Azure, GCP) get higher reliability scores.
    """
    if provider_type == ProviderType.HYPERSCALER:
        return 0.95
    elif provider_type == ProviderType.EU:
        return 0.85
    else:
        return 0.75


def compute_performance_score(
    vcpu: int, ram_gb: float, family: str | None, candidates: list[Candidate]
) -> float:
    """Compute performance score based on instance specs relative to candidate set.

    Normalizes vCPU and RAM separately, then combines them. Higher vCPU/RAM = better.
    """
    if not candidates:
        return 0.5

    max_vcpu = max(c.vcpu for c in candidates)
    max_ram = max(c.ram_gb for c in candidates)

    vcpu_score = vcpu / max_vcpu if max_vcpu > 0 else 0.5
    ram_score = ram_gb / max_ram if max_ram > 0 else 0.5

    base_score = (0.6 * vcpu_score) + (0.4 * ram_score)

    if family and family.upper() in ("C", "COMPUTE"):
        base_score = min(1.0, base_score * 1.05)

    return base_score


def compute_spot_availability_score(
    vcpu: int,
    ram_gb: float,
    provider_type: ProviderType,
    price_per_hour: float,
    candidates: list[Candidate],
) -> float:
    """Compute spot availability score (0-1, higher = more likely available as spot).

    Heuristics:
    - Larger/niche instances tend to have better spot availability
    - Hyperscalers have better spot markets
    - More expensive instances have better availability
    """
    if not candidates:
        return 0.7

    max_vcpu = max(c.vcpu for c in candidates)
    max_ram = max(c.ram_gb for c in candidates)
    max_price = max(c.price_per_hour for c in candidates)
    min_price = min(c.price_per_hour for c in candidates)
    price_range = max_price - min_price if max_price > min_price else 1.0

    size_score = min(
        1.0,
        (vcpu / max_vcpu if max_vcpu > 0 else 0.5) * 0.5
        + (ram_gb / max_ram if max_ram > 0 else 0.5) * 0.5,
    )
    price_score = (price_per_hour - min_price) / price_range if price_range > 0 else 0.5
    provider_boost = 0.1 if provider_type == ProviderType.HYPERSCALER else 0.0

    availability = (0.5 * size_score) + (0.5 * price_score) + provider_boost
    return min(1.0, max(0.3, availability))


def compute_interruption_risk_score(
    vcpu: int,
    ram_gb: float,
    price_per_hour: float,
    provider_type: ProviderType,
    candidates: list[Candidate],
) -> float:
    """Compute interruption risk score (0-1, higher = higher risk).

    Heuristics:
    - Smaller/cheaper instances = higher risk (more demand)
    - Regional providers may have less stable spot markets
    """
    if not candidates:
        return 0.5

    max_vcpu = max(c.vcpu for c in candidates)
    max_ram = max(c.ram_gb for c in candidates)
    max_price = max(c.price_per_hour for c in candidates)
    min_price = min(c.price_per_hour for c in candidates)
    price_range = max_price - min_price if max_price > min_price else 1.0

    size_risk = 1.0 - min(
        1.0,
        (vcpu / max_vcpu if max_vcpu > 0 else 0.5) * 0.5
        + (ram_gb / max_ram if max_ram > 0 else 0.5) * 0.5,
    )
    price_risk = 1.0 - ((price_per_hour - min_price) / price_range if price_range > 0 else 0.5)
    provider_risk = 0.0 if provider_type == ProviderType.HYPERSCALER else 0.15

    risk = (0.5 * size_risk) + (0.5 * price_risk) + provider_risk
    return min(1.0, max(0.1, risk))


def compute_sustainability_score(
    carbon_intensity: float | None,
    renewable_pct: float | None,
    candidates: list[Candidate],
) -> float | None:
    """Compute sustainability score in [0, 1] (higher = greener). Returns None if no data.

    Both sub-scores use relative normalization against the candidate set:
    - carbon_score: lower intensity = better (60% weight)
    - renewable_score: higher renewable % = better (40% weight)
    """
    if carbon_intensity is None and renewable_pct is None:
        return None

    weight_carbon = 0.6
    weight_renew = 0.4

    carbon_score = 0.5
    if carbon_intensity is not None:
        carbons = [c.carbon_intensity_g_co2_kwh for c in candidates if c.carbon_intensity_g_co2_kwh is not None]
        if len(carbons) >= 2:
            max_c = max(carbons)
            min_c = min(carbons)
            span = max_c - min_c
            if span > 0:
                carbon_score = (max_c - carbon_intensity) / span
            else:
                carbon_score = 0.5
        elif carbons:
            carbon_score = max(0.0, min(1.0, 1.0 - carbon_intensity / 800.0))

    renew_score = 0.5
    if renewable_pct is not None:
        renewables = [c.renewable_energy_pct for c in candidates if c.renewable_energy_pct is not None]
        if len(renewables) >= 2:
            max_r = max(renewables)
            min_r = min(renewables)
            span = max_r - min_r
            if span > 0:
                renew_score = (renewable_pct - min_r) / span
            else:
                renew_score = 0.5
        elif renewables:
            renew_score = renewable_pct

    if carbon_intensity is not None and renewable_pct is not None:
        return weight_carbon * carbon_score + weight_renew * renew_score
    elif carbon_intensity is not None:
        return carbon_score
    else:
        return renew_score
