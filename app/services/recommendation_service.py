"""Recommendation service: orchestrates candidate fetching, scoring, TCO, and ranking.

This module is the facade/orchestrator. Computation logic lives in:
- candidate_fetcher.py: SQL query building, sustainability enrichment
- scoring.py: individual scoring dimensions (reliability, performance, spot, sustainability)
- tco.py: total cost of ownership computation (egress, storage, public IP, OS license)
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.price import PurchaseModel
from app.schemas.recommendations import (
    RecommendationExplain,
    RecommendationItem,
    RecommendationRequest,
)
from app.services.candidate_fetcher import (
    Candidate,
    enrich_and_filter_sustainability,
    fetch_candidates,
)
from app.services.scoring import (
    USD_ONLY_PENALTY,
    compute_interruption_risk_score,
    compute_performance_score,
    compute_reliability_score,
    compute_spot_availability_score,
    compute_sustainability_score,
)
from app.services.tco import TcoResult, compute_tco


class RecommendationService:
    """Compute instance recommendations from min specs, region and price constraints."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_recommendations(self, req: RecommendationRequest) -> list[RecommendationItem]:
        """Filter by specs/region/price, score and return top recommendations."""
        candidates = await fetch_candidates(self._db, req)
        if not candidates:
            return []

        use_sustainability = (
            req.min_renewable_pct is not None
            or req.max_carbon_intensity is not None
            or req.providers_publish_carbon_only
        )
        if use_sustainability:
            candidates = await enrich_and_filter_sustainability(self._db, candidates, req)
            if not candidates:
                return []

        candidate_tcos, use_tco, candidates = await compute_tco(self._db, candidates, req)
        if use_tco and not candidates:
            return []

        weights = _compute_scoring_weights(req)
        scored = _score_candidates(candidates, req, candidate_tcos, use_tco, use_sustainability, weights)
        return _build_response(scored, req.limit, weights)


# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------


def _compute_scoring_weights(req: RecommendationRequest) -> dict:
    """Determine scoring mode (multi-criteria vs legacy) and normalize weights."""
    use_multi_criteria = (
        req.cost_weight is not None or req.performance_weight is not None or req.reliability_weight is not None
    )

    if use_multi_criteria:
        cost_w = req.cost_weight if req.cost_weight is not None else settings.recommendation_cost_weight
        perf_w = (
            req.performance_weight
            if req.performance_weight is not None
            else settings.recommendation_performance_weight
        )
        rel_w = (
            req.reliability_weight
            if req.reliability_weight is not None
            else settings.recommendation_reliability_weight
        )
        total = cost_w + perf_w + rel_w
        if total <= 0:
            cost_w = perf_w = rel_w = 1.0 / 3.0
            total = 1.0
        return {
            "multi": True,
            "cost_weight": cost_w / total,
            "perf_weight": perf_w / total,
            "rel_weight": rel_w / total,
            "price_weight": None,
            "fit_weight": None,
        }

    price_weight = req.price_weight if req.price_weight is not None else settings.recommendation_price_weight
    fit_weight = req.fit_weight if req.fit_weight is not None else settings.recommendation_fit_weight
    total = price_weight + fit_weight
    if total <= 0:
        price_weight = fit_weight = 0.5
        total = 1.0
    return {
        "multi": False,
        "cost_weight": None,
        "perf_weight": None,
        "rel_weight": None,
        "price_weight": price_weight / total,
        "fit_weight": fit_weight / total,
    }


# ---------------------------------------------------------------------------
# Score and rank
# ---------------------------------------------------------------------------


def _score_candidates(
    candidates: list[Candidate],
    req: RecommendationRequest,
    candidate_tcos: dict[int, TcoResult],
    use_tco: bool,
    use_sustainability: bool,
    weights: dict,
) -> list[tuple]:
    """Score and sort candidates. Returns list of tuples for response building."""
    if use_tco:
        min_price = min(candidate_tcos[i][0] for i in range(len(candidates)))
        max_price = max(candidate_tcos[i][0] for i in range(len(candidates)))
    else:
        min_price = min(c.price_per_hour for c in candidates)
        max_price = max(c.price_per_hour for c in candidates)
    price_range = max_price - min_price if max_price > min_price else 1.0

    preferred_set = set(req.preferred_providers) if req.preferred_providers else set()
    preference_multiplier = settings.recommendation_preference_multiplier

    try:
        purchase_model = PurchaseModel(req.purchase_model.lower())
        is_spot = purchase_model == PurchaseModel.SPOT
    except ValueError:
        is_spot = False

    scored: list[tuple] = []
    for i, c in enumerate(candidates):
        resource_fit = min(1.0, req.min_vcpu / c.vcpu) * min(1.0, req.min_ram_gb / c.ram_gb)
        cost_for_score = candidate_tcos[i][0] if use_tco else c.price_per_hour
        normalized_price = (max_price - cost_for_score) / price_range if price_range else 1.0

        spot_avail_score = None
        interrupt_risk_score = None
        if is_spot:
            spot_avail_score = compute_spot_availability_score(
                c.vcpu, c.ram_gb, c.provider_type, c.price_per_hour, candidates
            )
            interrupt_risk_score = compute_interruption_risk_score(
                c.vcpu, c.ram_gb, c.price_per_hour, c.provider_type, candidates
            )

        if weights["multi"]:
            cost_score = normalized_price
            perf_score = compute_performance_score(c.vcpu, c.ram_gb, c.family, candidates)
            rel_score = compute_reliability_score(c.provider_type)
            score = (
                (weights["cost_weight"] * cost_score)
                + (weights["perf_weight"] * perf_score)
                + (weights["rel_weight"] * rel_score)
            )
            perf_score_out = perf_score
            rel_score_out = rel_score
        else:
            score = (weights["price_weight"] * normalized_price) + (weights["fit_weight"] * resource_fit)
            perf_score_out = None
            rel_score_out = None

        if c.price_currency != "EUR":
            score *= USD_ONLY_PENALTY

        if is_spot and interrupt_risk_score is not None:
            score *= 1.0 - interrupt_risk_score * 0.3

        if c.provider_slug in preferred_set:
            score *= preference_multiplier

        sust_score = compute_sustainability_score(
            c.carbon_intensity_g_co2_kwh, c.renewable_energy_pct, candidates
        )
        if sust_score is not None and use_sustainability:
            score *= 1.0 + settings.sustainability_boost_max * sust_score

        tco_data = candidate_tcos[i] if use_tco else None
        scored.append((
            score,
            resource_fit,
            normalized_price,
            perf_score_out,
            rel_score_out,
            spot_avail_score,
            interrupt_risk_score,
            c,
            tco_data[0] if tco_data else None,
            tco_data[1] if tco_data else None,
            tco_data[2] if tco_data else None,
            tco_data[3] if tco_data else None,
            tco_data[4] if tco_data else None,
            tco_data[5] if tco_data else None,
            sust_score,
        ))

    scored.sort(key=lambda x: -x[0])
    return scored


# ---------------------------------------------------------------------------
# Build response
# ---------------------------------------------------------------------------


def _build_response(
    scored: list[tuple],
    limit: int,
    weights: dict,
) -> list[RecommendationItem]:
    """Convert scored tuples into RecommendationItem responses."""
    top = scored[:limit]
    return [
        RecommendationItem(
            instance_type_id=c.instance_type_id,
            instance_type_name=c.instance_type_name,
            provider_slug=c.provider_slug,
            region_id=c.region_id,
            region_slug=c.region_slug,
            vcpu=c.vcpu,
            ram_gb=c.ram_gb,
            arch=c.arch,
            score=score,
            explain=RecommendationExplain(
                resource_fit=resource_fit,
                normalized_price=normalized_price,
                price_weight=weights["price_weight"] or 0.0,
                fit_weight=weights["fit_weight"] or 0.0,
                performance_score=perf_score,
                reliability_score=rel_score,
                cost_weight=weights["cost_weight"],
                performance_weight=weights["perf_weight"],
                reliability_weight=weights["rel_weight"],
                spot_availability_score=spot_avail_score,
                interruption_risk_score=interrupt_risk_score,
                price_eur_per_hour=c.price_per_hour,
                region_is_eu=c.region_is_eu,
                tco_eur_per_hour=tco_val,
                egress_cost_eur_per_hour=egress_val,
                storage_cost_eur_per_hour=storage_val,
                public_ip_cost_eur_per_hour=public_ip_val,
                os_license_cost_eur_per_hour=os_license_val,
                cross_az_cost_eur_per_hour=cross_az_val,
                sustainability_score=sust_score,
            ),
        )
        for score, resource_fit, normalized_price, perf_score, rel_score, spot_avail_score, interrupt_risk_score, c, tco_val, egress_val, storage_val, public_ip_val, os_license_val, cross_az_val, sust_score in top
    ]
