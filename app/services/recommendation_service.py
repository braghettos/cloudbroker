"""Recommendation service: filter instance types and regions, score and rank."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    EgressRate,
    InstanceType,
    OsLicenseRate,
    Price,
    Provider,
    ProviderType,
    PublicIpRate,
    Region,
    StorageRate,
)
from app.models.price import PurchaseModel
from app.schemas.recommendations import (
    RecommendationExplain,
    RecommendationItem,
    RecommendationRequest,
)


# Small score penalty when only USD price is available (no EUR)
USD_ONLY_PENALTY = 0.95


def _compute_reliability_score(provider_type: ProviderType) -> float:
    """Compute reliability score based on provider type.

    HYPERSCALER providers (AWS, Azure, GCP) get higher reliability scores.
    """
    if provider_type == ProviderType.HYPERSCALER:
        return 0.95  # High reliability for hyperscalers
    elif provider_type == ProviderType.EU:
        return 0.85  # Good reliability for EU-focused providers
    else:
        return 0.75  # Baseline for regional providers


def _compute_performance_score(vcpu: int, ram_gb: float, family: str | None, candidates: list[_Candidate]) -> float:
    """Compute performance score based on instance specs relative to candidate set.

    Normalizes vCPU and RAM separately, then combines them. Higher vCPU/RAM = better performance.
    """
    if not candidates:
        return 0.5

    max_vcpu = max(c.vcpu for c in candidates)
    max_ram = max(c.ram_gb for c in candidates)

    vcpu_score = vcpu / max_vcpu if max_vcpu > 0 else 0.5
    ram_score = ram_gb / max_ram if max_ram > 0 else 0.5

    # Combine vCPU and RAM (weighted average, slightly favor vCPU)
    base_score = (0.6 * vcpu_score) + (0.4 * ram_score)

    # Small boost for compute-optimized families (C-series, etc.)
    if family and family.upper() in ("C", "COMPUTE"):
        base_score = min(1.0, base_score * 1.05)

    return base_score


def _compute_spot_availability_score(
    vcpu: int,
    ram_gb: float,
    provider_type: ProviderType,
    price_per_hour: float,
    candidates: list[_Candidate],
) -> float:
    """Compute spot availability score (0-1, higher = more likely to be available as spot).

    Heuristics:
    - Larger/niche instances (higher vCPU/RAM) tend to have better spot availability
    - Hyperscalers generally have better spot availability
    - More expensive instances (relative to set) tend to have better availability
    """
    if not candidates:
        return 0.7  # Default moderate availability

    max_vcpu = max(c.vcpu for c in candidates)
    max_ram = max(c.ram_gb for c in candidates)
    max_price = max(c.price_per_hour for c in candidates)
    min_price = min(c.price_per_hour for c in candidates)
    price_range = max_price - min_price if max_price > min_price else 1.0

    # Larger instances = better availability (less competition)
    size_score = min(
        1.0, (vcpu / max_vcpu if max_vcpu > 0 else 0.5) * 0.5 + (ram_gb / max_ram if max_ram > 0 else 0.5) * 0.5
    )

    # More expensive instances = better availability (less demand)
    price_score = (price_per_hour - min_price) / price_range if price_range > 0 else 0.5

    # Provider boost: hyperscalers have better spot markets
    provider_boost = 0.1 if provider_type == ProviderType.HYPERSCALER else 0.0

    availability = (0.5 * size_score) + (0.5 * price_score) + provider_boost
    return min(1.0, max(0.3, availability))  # Clamp between 0.3 and 1.0


def _compute_egress_cost_eur_per_hour(
    candidate_provider: str,
    candidate_region: str,
    data_source_provider: str,
    data_source_region: str,
    estimated_gb_per_hour: float,
    egress_rate: EgressRate | None,
) -> float:
    """Compute egress cost (EUR/h) for a candidate relative to data source.

    - Same provider + same region: 0 (internal transfer free)
    - Same provider, different region: cross_region_eur_per_gb if set, else egress_eur_per_gb
    - Different provider: egress_eur_per_gb
    """
    if not egress_rate or estimated_gb_per_hour <= 0:
        return 0.0

    ds_prov = (data_source_provider or "").strip().lower()
    ds_reg = (data_source_region or "").strip().lower()
    cand_prov = candidate_provider.strip().lower()
    cand_reg = (candidate_region or "").strip().lower()

    if ds_prov and cand_prov and ds_prov == cand_prov:
        if ds_reg and cand_reg and ds_reg == cand_reg:
            return 0.0  # Same provider + same region: free
        # Same provider, different region
        rate = egress_rate.cross_region_eur_per_gb
        if rate is None:
            rate = egress_rate.egress_eur_per_gb or 0.0
    else:
        # Different provider: internet egress
        rate = egress_rate.egress_eur_per_gb or 0.0

    return estimated_gb_per_hour * rate


def _compute_storage_cost_eur_per_hour(
    storage_gb: float,
    storage_type: str,
    provider_slug: str,
    region_slug: str | None,
    storage_rates: dict[tuple[str, str | None, str], float],
) -> float:
    """Compute storage cost (EUR/h). storage_gb * eur_per_gb_month / 730."""
    if storage_gb <= 0:
        return 0.0
    key_region = (provider_slug, region_slug, storage_type)
    key_provider = (provider_slug, None, storage_type)
    eur_per_gb_month = storage_rates.get(key_region) or storage_rates.get(key_provider)
    if eur_per_gb_month is None:
        return 0.0
    return storage_gb * eur_per_gb_month / 730.0


def _compute_public_ip_cost_eur_per_hour(
    include_public_ip: bool,
    provider_slug: str,
    public_ip_rates: dict[str, float],
) -> float:
    if not include_public_ip:
        return 0.0
    return public_ip_rates.get(provider_slug, 0.0)


def _compute_os_license_cost_eur_per_hour(
    vcpu: int,
    os_type: str,
    provider_slug: str,
    os_license_rates: dict[tuple[str, str], float],
) -> float:
    if not os_type or os_type.lower() == "linux":
        return 0.0
    rate = os_license_rates.get((provider_slug, os_type.lower()))
    if rate is None:
        return 0.0
    return vcpu * rate


def _compute_cross_az_cost_eur_per_hour(
    estimated_gb_per_hour: float,
    provider_slug: str,
    egress_rate: EgressRate | None,
) -> float:
    if not egress_rate or estimated_gb_per_hour <= 0:
        return 0.0
    rate = egress_rate.cross_az_eur_per_gb
    if rate is None:
        return 0.0
    return estimated_gb_per_hour * rate


def _compute_interruption_risk_score(
    vcpu: int,
    ram_gb: float,
    price_per_hour: float,
    provider_type: ProviderType,
    candidates: list[_Candidate],
) -> float:
    """Compute interruption risk score (0-1, higher = higher risk of interruption).

    Heuristics:
    - Smaller/cheaper instances = higher interruption risk (more demand)
    - Popular instance families = higher risk
    - Regional providers may have less stable spot markets
    """
    if not candidates:
        return 0.5  # Default moderate risk

    max_vcpu = max(c.vcpu for c in candidates)
    max_ram = max(c.ram_gb for c in candidates)
    max_price = max(c.price_per_hour for c in candidates)
    min_price = min(c.price_per_hour for c in candidates)
    price_range = max_price - min_price if max_price > min_price else 1.0

    # Smaller instances = higher risk (more competition)
    size_risk = 1.0 - min(
        1.0, (vcpu / max_vcpu if max_vcpu > 0 else 0.5) * 0.5 + (ram_gb / max_ram if max_ram > 0 else 0.5) * 0.5
    )

    # Cheaper instances = higher risk (more demand)
    price_risk = 1.0 - ((price_per_hour - min_price) / price_range if price_range > 0 else 0.5)

    # Provider risk: hyperscalers have more stable spot markets
    provider_risk = 0.0 if provider_type == ProviderType.HYPERSCALER else 0.15

    risk = (0.5 * size_risk) + (0.5 * price_risk) + provider_risk
    return min(1.0, max(0.1, risk))  # Clamp between 0.1 and 1.0


@dataclass
class _Candidate:
    """One (instance_type, region, price) row from the DB."""

    instance_type_id: int
    instance_type_name: str
    provider_slug: str
    provider_type: ProviderType  # For reliability scoring
    region_id: int
    region_slug: str
    vcpu: int
    ram_gb: float
    arch: str | None
    family: str | None  # For performance scoring
    gpu_count: int | None
    price_per_hour: float  # EUR-normalized for comparison/scoring
    price_currency: str  # Original currency (EUR or USD)
    region_is_eu: bool


class RecommendationService:
    """Compute instance recommendations from min specs, region and price constraints."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def _fetch_egress_rates(self) -> dict[str, EgressRate]:
        """Fetch egress rates by provider slug. Returns empty dict if none configured."""
        stmt = select(EgressRate)
        result = await self._db.execute(stmt)
        rows = result.scalars().all()
        return {r.provider_slug: r for r in rows}

    async def _fetch_storage_rates(
        self,
    ) -> dict[tuple[str, str | None, str], float]:
        """Fetch storage rates: (provider_slug, region_slug, storage_type) -> eur_per_gb_month."""
        stmt = select(StorageRate)
        result = await self._db.execute(stmt)
        rows = result.scalars().all()
        return {(r.provider_slug, r.region_slug, r.storage_type): r.eur_per_gb_month for r in rows}

    async def _fetch_public_ip_rates(self) -> dict[str, float]:
        """Fetch public IP rates by provider slug: provider_slug -> eur_per_hour."""
        stmt = select(PublicIpRate)
        result = await self._db.execute(stmt)
        rows = result.scalars().all()
        return {r.provider_slug: r.eur_per_hour for r in rows}

    async def _fetch_os_license_rates(
        self,
    ) -> dict[tuple[str, str], float]:
        """Fetch OS license rates: (provider_slug, os_type) -> eur_per_vcpu_hour."""
        stmt = select(OsLicenseRate)
        result = await self._db.execute(stmt)
        rows = result.scalars().all()
        return {(r.provider_slug, r.os_type): r.eur_per_vcpu_hour for r in rows}

    async def get_recommendations(self, req: RecommendationRequest) -> list[RecommendationItem]:
        """Filter by specs/region/price, score and return top recommendations."""
        candidates = await self._fetch_candidates(req)
        if not candidates:
            return []

        # TCO context: when any cost component is provided, compute full TCO for scoring
        use_egress_tco = (
            req.data_source_provider is not None
            and req.data_source_region is not None
            and req.estimated_egress_gb_per_hour is not None
            and req.estimated_egress_gb_per_hour > 0
        )
        use_storage_tco = req.min_storage_gb is not None and req.min_storage_gb > 0
        use_public_ip_tco = req.include_public_ip is True
        use_os_tco = req.os_type is not None and req.os_type.lower() != "linux"
        use_cross_az_tco = req.estimated_cross_az_gb_per_hour is not None and req.estimated_cross_az_gb_per_hour > 0
        use_tco = use_egress_tco or use_storage_tco or use_public_ip_tco or use_os_tco or use_cross_az_tco

        storage_type = (req.storage_type or "ssd").lower().strip()
        min_storage_gb = req.min_storage_gb or 0.0
        os_type = (req.os_type or "linux").lower().strip()

        egress_rates: dict[str, EgressRate] = {}
        storage_rates: dict[tuple[str, str | None, str], float] = {}
        public_ip_rates: dict[str, float] = {}
        os_license_rates: dict[tuple[str, str], float] = {}

        if use_egress_tco or use_cross_az_tco:
            egress_rates = await self._fetch_egress_rates()
            if use_egress_tco and not egress_rates:
                use_egress_tco = False
        if use_storage_tco:
            storage_rates = await self._fetch_storage_rates()
        if use_public_ip_tco:
            public_ip_rates = await self._fetch_public_ip_rates()
        if use_os_tco:
            os_license_rates = await self._fetch_os_license_rates()

        # Compute TCO per candidate when enabled
        # (tco, egress_cost, storage_cost, public_ip_cost, os_license_cost, cross_az_cost)
        candidate_tcos: dict[int, tuple[float, float, float, float, float, float]] = {}
        if use_tco:
            for i, c in enumerate(candidates):
                er = egress_rates.get(c.provider_slug)
                egress_cost = (
                    _compute_egress_cost_eur_per_hour(
                        c.provider_slug,
                        c.region_slug,
                        req.data_source_provider or "",
                        req.data_source_region or "",
                        req.estimated_egress_gb_per_hour or 0.0,
                        er,
                    )
                    if use_egress_tco
                    else 0.0
                )
                storage_cost = (
                    _compute_storage_cost_eur_per_hour(
                        min_storage_gb,
                        storage_type,
                        c.provider_slug,
                        c.region_slug,
                        storage_rates,
                    )
                    if use_storage_tco
                    else 0.0
                )
                public_ip_cost = (
                    _compute_public_ip_cost_eur_per_hour(
                        use_public_ip_tco,
                        c.provider_slug,
                        public_ip_rates,
                    )
                    if use_public_ip_tco
                    else 0.0
                )
                os_license_cost = (
                    _compute_os_license_cost_eur_per_hour(
                        c.vcpu,
                        os_type,
                        c.provider_slug,
                        os_license_rates,
                    )
                    if use_os_tco
                    else 0.0
                )
                cross_az_cost = (
                    _compute_cross_az_cost_eur_per_hour(
                        req.estimated_cross_az_gb_per_hour or 0.0,
                        c.provider_slug,
                        er,
                    )
                    if use_cross_az_tco
                    else 0.0
                )
                tco = c.price_per_hour + egress_cost + storage_cost + public_ip_cost + os_license_cost + cross_az_cost
                candidate_tcos[i] = (
                    tco,
                    egress_cost,
                    storage_cost,
                    public_ip_cost,
                    os_license_cost,
                    cross_az_cost,
                )

            # Filter by max TCO when set
            if req.max_tco_eur_per_hour is not None and req.max_tco_eur_per_hour > 0:
                kept: list[tuple[int, _Candidate]] = []
                new_tcos: dict[int, tuple[float, float, float, float, float, float]] = {}
                for i, c in enumerate(candidates):
                    tco, *costs = candidate_tcos[i]
                    if tco <= req.max_tco_eur_per_hour:
                        new_idx = len(kept)
                        kept.append((i, c))
                        new_tcos[new_idx] = (tco, *costs)
                candidates = [c for _, c in kept]
                candidate_tcos = new_tcos
                if not candidates:
                    return []

        # Determine if using multi-criteria (cost/performance/reliability) or legacy (price/fit) scoring
        use_multi_criteria = (
            req.cost_weight is not None or req.performance_weight is not None or req.reliability_weight is not None
        )

        if use_multi_criteria:
            # Multi-criteria scoring: cost, performance, reliability
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
            cost_weight_norm = cost_w / total
            perf_weight_norm = perf_w / total
            rel_weight_norm = rel_w / total

            # Legacy weights set to None for explain
            price_weight_norm = None
            fit_weight_norm = None
        else:
            # Legacy scoring: price + fit
            price_weight = req.price_weight if req.price_weight is not None else settings.recommendation_price_weight
            fit_weight = req.fit_weight if req.fit_weight is not None else settings.recommendation_fit_weight
            total = price_weight + fit_weight
            if total <= 0:
                price_weight = fit_weight = 0.5
                total = 1.0
            price_weight_norm = price_weight / total
            fit_weight_norm = fit_weight / total

            # Multi-criteria weights set to None for explain
            cost_weight_norm = None
            perf_weight_norm = None
            rel_weight_norm = None

        # Use TCO for cost scoring when placement context provided, else instance price
        if use_tco:
            min_price = min(candidate_tcos[i][0] for i in range(len(candidates)))
            max_price = max(candidate_tcos[i][0] for i in range(len(candidates)))
        else:
            min_price = min(c.price_per_hour for c in candidates)
            max_price = max(c.price_per_hour for c in candidates)
        price_range = max_price - min_price if max_price > min_price else 1.0

        # Build sets for fast lookup
        preferred_set = set(req.preferred_providers) if req.preferred_providers else set()
        preference_multiplier = settings.recommendation_preference_multiplier

        # Determine if this is a spot request
        try:
            purchase_model = PurchaseModel(req.purchase_model.lower())
            is_spot = purchase_model == PurchaseModel.SPOT
        except ValueError:
            is_spot = False

        scored: list[
            tuple[
                float,
                float,
                float,
                float | None,
                float | None,
                float | None,
                float | None,
                _Candidate,
                float | None,
                float | None,
            ]
        ] = []
        for i, c in enumerate(candidates):
            resource_fit = min(1.0, req.min_vcpu / c.vcpu) * min(1.0, req.min_ram_gb / c.ram_gb)
            cost_for_score = candidate_tcos[i][0] if use_tco else c.price_per_hour
            normalized_price = (max_price - cost_for_score) / price_range if price_range else 1.0

            # Compute spot risk scores if this is a spot request
            spot_avail_score = None
            interrupt_risk_score = None
            if is_spot:
                spot_avail_score = _compute_spot_availability_score(
                    c.vcpu, c.ram_gb, c.provider_type, c.price_per_hour, candidates
                )
                interrupt_risk_score = _compute_interruption_risk_score(
                    c.vcpu, c.ram_gb, c.price_per_hour, c.provider_type, candidates
                )

            if use_multi_criteria:
                # Multi-criteria: cost (inverse of price), performance, reliability
                cost_score = normalized_price  # Higher = cheaper = better
                perf_score = _compute_performance_score(c.vcpu, c.ram_gb, c.family, candidates)
                rel_score = _compute_reliability_score(c.provider_type)
                score = (
                    (cost_weight_norm * cost_score) + (perf_weight_norm * perf_score) + (rel_weight_norm * rel_score)
                )
                perf_score_out = perf_score
                rel_score_out = rel_score
            else:
                # Legacy: price + fit
                score = (price_weight_norm * normalized_price) + (fit_weight_norm * resource_fit)
                perf_score_out = None
                rel_score_out = None

            if c.price_currency != "EUR":
                score *= USD_ONLY_PENALTY

            # Apply spot risk penalty: reduce score for high interruption risk
            if is_spot and interrupt_risk_score is not None:
                # Penalize high-risk instances: multiply by (1 - risk * 0.3)
                # This means a risk of 1.0 reduces score by 30%, risk of 0.5 reduces by 15%
                score *= 1.0 - interrupt_risk_score * 0.3

            # Apply preference multiplier for preferred providers
            if c.provider_slug in preferred_set:
                score *= preference_multiplier
            tco_data = candidate_tcos[i] if use_tco else None
            tco_val = tco_data[0] if tco_data else None
            egress_val = tco_data[1] if tco_data else None
            storage_val = tco_data[2] if tco_data else None
            public_ip_val = tco_data[3] if tco_data else None
            os_license_val = tco_data[4] if tco_data else None
            cross_az_val = tco_data[5] if tco_data else None
            scored.append(
                (
                    score,
                    resource_fit,
                    normalized_price,
                    perf_score_out,
                    rel_score_out,
                    spot_avail_score,
                    interrupt_risk_score,
                    c,
                    tco_val,
                    egress_val,
                    storage_val,
                    public_ip_val,
                    os_license_val,
                    cross_az_val,
                )
            )

        scored.sort(key=lambda x: -x[0])
        top = scored[: req.limit]

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
                    price_weight=price_weight_norm or 0.0,
                    fit_weight=fit_weight_norm or 0.0,
                    performance_score=perf_score,
                    reliability_score=rel_score,
                    cost_weight=cost_weight_norm,
                    performance_weight=perf_weight_norm,
                    reliability_weight=rel_weight_norm,
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
                ),
            )
            for score, resource_fit, normalized_price, perf_score, rel_score, spot_avail_score, interrupt_risk_score, c, tco_val, egress_val, storage_val, public_ip_val, os_license_val, cross_az_val in top
        ]

    async def _fetch_candidates(self, req: RecommendationRequest) -> list[_Candidate]:
        """Load (instance_type, region, latest price) matching filters."""
        # arch is already normalized to canonical values (x86_64, arm64) by the schema validator
        arch_val = req.arch

        # Parse purchase_model from request
        try:
            purchase_model = PurchaseModel(req.purchase_model.lower())
        except ValueError:
            # Default to on_demand if invalid
            purchase_model = PurchaseModel.ON_DEMAND

        # Subquery: latest price per (instance_type_id, region_id) for requested purchase_model
        latest = (
            select(
                Price.instance_type_id,
                Price.region_id,
                func.max(Price.timestamp).label("max_ts"),
            )
            .where(Price.purchase_model == purchase_model)
            .group_by(Price.instance_type_id, Price.region_id)
        )
        latest_subq = latest.subquery()

        # Use EUR price when available, else price_per_hour (stored in EUR or converted)
        effective_price = func.coalesce(Price.price_eur_per_hour, Price.price_per_hour)

        # Main query: InstanceType -> Price (latest) -> Region -> Provider
        stmt = (
            select(
                InstanceType.id.label("instance_type_id"),
                InstanceType.name.label("instance_type_name"),
                Provider.slug.label("provider_slug"),
                Provider.type.label("provider_type"),
                Region.id.label("region_id"),
                Region.slug.label("region_slug"),
                InstanceType.vcpu,
                InstanceType.ram_gb,
                InstanceType.arch,
                InstanceType.family,
                InstanceType.gpu_count,
                effective_price.label("price_per_hour"),
                Price.currency.label("price_currency"),
                Region.is_eu.label("region_is_eu"),
            )
            .select_from(InstanceType)
            .join(Price, InstanceType.id == Price.instance_type_id)
            .join(
                latest_subq,
                and_(
                    Price.instance_type_id == latest_subq.c.instance_type_id,
                    Price.region_id == latest_subq.c.region_id,
                    Price.timestamp == latest_subq.c.max_ts,
                ),
            )
            .join(Region, Price.region_id == Region.id)
            .join(Provider, InstanceType.provider_id == Provider.id)
            .where(InstanceType.vcpu >= req.min_vcpu)
            .where(InstanceType.ram_gb >= req.min_ram_gb)
            .where(InstanceType.arch == arch_val)
            .where(Price.purchase_model == purchase_model)
            .where(effective_price <= req.max_price_eur_per_hour)
        )
        if req.region_constraint.value == "EU":
            stmt = stmt.where(Region.is_eu.is_(True))

        # Restrict to allowed providers only (when specified)
        if req.allowed_providers:
            stmt = stmt.where(Provider.slug.in_(req.allowed_providers))

        # Filter excluded providers
        if req.excluded_providers:
            stmt = stmt.where(~Provider.slug.in_(req.excluded_providers))

        # Filter excluded instance types (e.g. after provisioning failure)
        if req.excluded_instance_types:
            stmt = stmt.where(~InstanceType.name.in_(req.excluded_instance_types))

        # Azure ARM-only sizes (pls, ps_v5, plds) may be mislabeled as x86_64 in the DB.
        # Exclude them when requesting x86_64 to avoid "Arm64 VM size with x64 image" failures.
        if arch_val == "x86_64":
            stmt = stmt.where(
                or_(
                    Provider.slug != "azure",
                    and_(
                        ~InstanceType.name.ilike("%pls%"),
                        ~InstanceType.name.ilike("%ps_v5%"),
                        ~InstanceType.name.ilike("%plds%"),
                    ),
                )
            )

        # Filter by min_gpu_count when requested
        if req.min_gpu_count is not None and req.min_gpu_count > 0:
            stmt = stmt.where(
                InstanceType.gpu_count.isnot(None),
                InstanceType.gpu_count >= req.min_gpu_count,
            )

        # Filter by gpu_type (substring match on instance type name)
        if req.gpu_type:
            gpu_pattern = f"%{req.gpu_type}%"
            stmt = stmt.where(InstanceType.name.ilike(gpu_pattern))

        result = await self._db.execute(stmt)
        rows = result.all()

        return [
            _Candidate(
                instance_type_id=r.instance_type_id,
                instance_type_name=r.instance_type_name,
                provider_slug=r.provider_slug,
                provider_type=r.provider_type,
                region_id=r.region_id,
                region_slug=r.region_slug,
                vcpu=r.vcpu,
                ram_gb=r.ram_gb,
                arch=r.arch,
                family=r.family,
                gpu_count=r.gpu_count,
                price_per_hour=r.price_per_hour,
                price_currency=r.price_currency or "EUR",
                region_is_eu=r.region_is_eu,
            )
            for r in rows
        ]
