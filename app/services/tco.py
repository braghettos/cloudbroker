"""Total Cost of Ownership (TCO) computation for recommendation candidates.

Computes cost components beyond base instance price: egress, storage,
public IP, OS license, and cross-AZ transfer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EgressRate, OsLicenseRate, PublicIpRate, StorageRate
from app.schemas.recommendations import RecommendationRequest

if TYPE_CHECKING:
    from app.services.candidate_fetcher import Candidate


# Type alias for TCO tuple: (total, egress, storage, public_ip, os_license, cross_az)
TcoResult = tuple[float, float, float, float, float, float]


# ---------------------------------------------------------------------------
# Cost component functions
# ---------------------------------------------------------------------------


def compute_egress_cost_eur_per_hour(
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
            return 0.0
        rate = egress_rate.cross_region_eur_per_gb
        if rate is None:
            rate = egress_rate.egress_eur_per_gb or 0.0
    else:
        rate = egress_rate.egress_eur_per_gb or 0.0

    return estimated_gb_per_hour * rate


def compute_storage_cost_eur_per_hour(
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


def compute_public_ip_cost_eur_per_hour(
    include_public_ip: bool,
    provider_slug: str,
    public_ip_rates: dict[str, float],
) -> float:
    if not include_public_ip:
        return 0.0
    return public_ip_rates.get(provider_slug, 0.0)


def compute_os_license_cost_eur_per_hour(
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


def compute_cross_az_cost_eur_per_hour(
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


# ---------------------------------------------------------------------------
# Rate fetching
# ---------------------------------------------------------------------------


async def fetch_egress_rates(db: AsyncSession) -> dict[str, EgressRate]:
    """Fetch egress rates by provider slug."""
    stmt = select(EgressRate)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return {r.provider_slug: r for r in rows}


async def fetch_storage_rates(db: AsyncSession) -> dict[tuple[str, str | None, str], float]:
    """Fetch storage rates: (provider_slug, region_slug, storage_type) -> eur_per_gb_month."""
    stmt = select(StorageRate)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return {(r.provider_slug, r.region_slug, r.storage_type): r.eur_per_gb_month for r in rows}


async def fetch_public_ip_rates(db: AsyncSession) -> dict[str, float]:
    """Fetch public IP rates by provider slug: provider_slug -> eur_per_hour."""
    stmt = select(PublicIpRate)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return {r.provider_slug: r.eur_per_hour for r in rows}


async def fetch_os_license_rates(db: AsyncSession) -> dict[tuple[str, str], float]:
    """Fetch OS license rates: (provider_slug, os_type) -> eur_per_vcpu_hour."""
    stmt = select(OsLicenseRate)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return {(r.provider_slug, r.os_type): r.eur_per_vcpu_hour for r in rows}


# ---------------------------------------------------------------------------
# TCO orchestrator
# ---------------------------------------------------------------------------


async def compute_tco(
    db: AsyncSession,
    candidates: list[Candidate],
    req: RecommendationRequest,
) -> tuple[dict[int, TcoResult], bool, list[Candidate]]:
    """Compute TCO per candidate when cost-component context is provided.

    Returns (tco_dict, use_tco_flag, filtered_candidates).
    """
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

    candidate_tcos: dict[int, TcoResult] = {}
    if not use_tco:
        return candidate_tcos, False, candidates

    storage_type = (req.storage_type or "ssd").lower().strip()
    min_storage_gb = req.min_storage_gb or 0.0
    os_type = (req.os_type or "linux").lower().strip()

    egress_rates: dict[str, EgressRate] = {}
    storage_rates: dict[tuple[str, str | None, str], float] = {}
    public_ip_rates: dict[str, float] = {}
    os_license_rates: dict[tuple[str, str], float] = {}

    if use_egress_tco or use_cross_az_tco:
        egress_rates = await fetch_egress_rates(db)
        if use_egress_tco and not egress_rates:
            use_egress_tco = False
    if use_storage_tco:
        storage_rates = await fetch_storage_rates(db)
    if use_public_ip_tco:
        public_ip_rates = await fetch_public_ip_rates(db)
    if use_os_tco:
        os_license_rates = await fetch_os_license_rates(db)

    for i, c in enumerate(candidates):
        er = egress_rates.get(c.provider_slug)
        egress_cost = (
            compute_egress_cost_eur_per_hour(
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
            compute_storage_cost_eur_per_hour(
                min_storage_gb, storage_type, c.provider_slug, c.region_slug, storage_rates
            )
            if use_storage_tco
            else 0.0
        )
        public_ip_cost = (
            compute_public_ip_cost_eur_per_hour(use_public_ip_tco, c.provider_slug, public_ip_rates)
            if use_public_ip_tco
            else 0.0
        )
        os_license_cost = (
            compute_os_license_cost_eur_per_hour(c.vcpu, os_type, c.provider_slug, os_license_rates)
            if use_os_tco
            else 0.0
        )
        cross_az_cost = (
            compute_cross_az_cost_eur_per_hour(req.estimated_cross_az_gb_per_hour or 0.0, c.provider_slug, er)
            if use_cross_az_tco
            else 0.0
        )
        tco = c.price_per_hour + egress_cost + storage_cost + public_ip_cost + os_license_cost + cross_az_cost
        candidate_tcos[i] = (tco, egress_cost, storage_cost, public_ip_cost, os_license_cost, cross_az_cost)

    if req.max_tco_eur_per_hour is not None and req.max_tco_eur_per_hour > 0:
        kept: list[tuple[int, Candidate]] = []
        new_tcos: dict[int, TcoResult] = {}
        for i, c in enumerate(candidates):
            tco, *costs = candidate_tcos[i]
            if tco <= req.max_tco_eur_per_hour:
                new_idx = len(kept)
                kept.append((i, c))
                new_tcos[new_idx] = (tco, *costs)
        candidates = [c for _, c in kept]
        candidate_tcos = new_tcos

    return candidate_tcos, True, candidates
