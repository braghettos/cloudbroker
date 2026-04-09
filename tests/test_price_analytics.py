"""Deep tests for Price Analytics: verify data shown in UI are real.

Validates that price changes and trends represent actual price movements,
not product mix artifacts (e.g. Linux vs Windows, spot vs on-demand).
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InstanceType, Price, Provider, Region
from app.models.price import PurchaseModel
from app.models.provider import ProviderType
from app.services.price_analytics import (
    SUSPICIOUS_CHANGE_PERCENT,
    detect_price_changes,
    validate_price_changes_data_quality,
)
from app.core.database import AsyncSessionLocal

# Prefix for seeded test data (unique per run; rolled back, never persisted)
_TEST_SLUG_PREFIX = "test-pa-"


async def _seed_price_history(
    db: AsyncSession,
    *,
    old_price_eur: float,
    new_price_eur: float,
    hours_ago_old: int = 48,
    hours_ago_new: int = 24,
) -> tuple[Provider, Region, InstanceType]:
    """Seed provider, region, instance type, and two price rows. Returns created entities."""
    slug_suffix = str(uuid.uuid4())[:8]
    provider = Provider(
        slug=f"{_TEST_SLUG_PREFIX}provider-{slug_suffix}",
        name="Test Provider",
        type=ProviderType.REGIONAL,
    )
    db.add(provider)
    await db.flush()

    region = Region(
        provider_id=provider.id,
        slug=f"{_TEST_SLUG_PREFIX}region-{slug_suffix}",
        name="Test Region",
        is_eu=True,
    )
    db.add(region)
    await db.flush()

    it = InstanceType(
        provider_id=provider.id,
        name=f"{_TEST_SLUG_PREFIX}instance-{slug_suffix}",
        vcpu=2,
        ram_gb=4.0,
    )
    db.add(it)
    await db.flush()

    now = datetime.datetime.now(datetime.timezone.utc)
    ts_old = now - datetime.timedelta(hours=hours_ago_old)
    ts_new = now - datetime.timedelta(hours=hours_ago_new)

    for price_eur, ts in [(old_price_eur, ts_old), (new_price_eur, ts_new)]:
        p = Price(
            instance_type_id=it.id,
            region_id=region.id,
            purchase_model=PurchaseModel.ON_DEMAND,
            currency="EUR",
            price_per_hour=price_eur,
            price_eur_per_hour=price_eur,
            timestamp=ts,
        )
        db.add(p)
    await db.flush()
    return (provider, region, it)


@pytest.fixture
async def db_session():
    """Provide a database session for tests. Rolls back after each test so no data persists."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest.mark.asyncio
async def test_real_price_change_detected_correctly(db_session: AsyncSession):
    """With seeded real data (5% increase), detect_price_changes returns correct values."""
    provider, region, it = await _seed_price_history(
        db_session,
        old_price_eur=0.10,
        new_price_eur=0.105,  # 5% increase
        hours_ago_old=48,
        hours_ago_new=24,
    )
    it_name, region_slug = it.name, region.slug

    changes = await detect_price_changes(
        db_session,
        purchase_model=PurchaseModel.ON_DEMAND,
        since=None,
    )

    our_changes = [c for c in changes if c.instance_type_name == it_name and c.region_slug == region_slug]
    assert len(our_changes) >= 1, f"Expected at least one change for {it_name}, got {len(changes)} total"

    c = our_changes[0]
    assert c.old_price_eur_per_hour == pytest.approx(0.10, rel=1e-6)
    assert c.new_price_eur_per_hour == pytest.approx(0.105, rel=1e-6)
    assert c.price_change_percent == pytest.approx(5.0, rel=0.1)
    assert c.price_change_eur_per_hour == pytest.approx(0.005, rel=1e-6)


@pytest.mark.asyncio
async def test_suspicious_product_mix_flagged_by_validation(db_session: AsyncSession):
    """Data that looks like product mix (10x change) is flagged as suspicious."""
    provider, region, it = await _seed_price_history(
        db_session,
        old_price_eur=0.0165,  # Spot-like
        new_price_eur=0.1674,  # On-demand-like (~10x)
        hours_ago_old=48,
        hours_ago_new=24,
    )
    it_name = it.name

    changes = await detect_price_changes(
        db_session,
        purchase_model=PurchaseModel.ON_DEMAND,
        since=None,
    )

    our_changes = [c for c in changes if c.instance_type_name == it_name]
    assert len(our_changes) >= 1

    plausible, suspicious = validate_price_changes_data_quality(our_changes)
    assert len(suspicious) >= 1, "10x change should be flagged as suspicious (possible product mix)"
    assert any(abs(c.price_change_percent) > SUSPICIOUS_CHANGE_PERCENT for c in suspicious)


@pytest.mark.asyncio
async def test_plausible_changes_pass_validation(db_session: AsyncSession):
    """Realistic price changes (<500%) pass data quality validation."""
    provider, region, it = await _seed_price_history(
        db_session,
        old_price_eur=0.08,
        new_price_eur=0.084,  # 5% increase
        hours_ago_old=48,
        hours_ago_new=24,
    )
    it_name = it.name

    changes = await detect_price_changes(
        db_session,
        purchase_model=PurchaseModel.ON_DEMAND,
        since=None,
    )

    our_changes = [c for c in changes if c.instance_type_name == it_name]
    assert len(our_changes) >= 1

    plausible, suspicious = validate_price_changes_data_quality(our_changes)
    assert len(suspicious) == 0, "5% change should be plausible"
    assert len(plausible) >= 1


@pytest.mark.asyncio
async def test_price_analytics_api_response_matches_ui_structure(client, monkeypatch):
    """API response structure matches what the UI expects (PriceChangesResponse)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    response = await client.get(
        "/api/price-analytics/changes?since_hours=168",
        headers={"X-API-Key": "dev-bypass"},
    )
    assert response.status_code == 200
    data = response.json()

    # UI expects: count, purchase_model, since_hours, changes[]
    assert "count" in data
    assert "purchase_model" in data
    assert "since_hours" in data
    assert "changes" in data
    assert isinstance(data["changes"], list)
    assert data["since_hours"] == 168

    for change in data["changes"]:
        assert "instance_type_id" in change
        assert "instance_type_name" in change
        assert "region_id" in change
        assert "region_slug" in change
        assert "purchase_model" in change
        assert "old_price_eur_per_hour" in change
        assert "new_price_eur_per_hour" in change
        assert "price_change_eur_per_hour" in change
        assert "price_change_percent" in change
        assert "timestamp" in change
        # Prices should be non-negative when present
        if change.get("old_price_eur_per_hour") is not None:
            assert change["old_price_eur_per_hour"] >= 0
        if change.get("new_price_eur_per_hour") is not None:
            assert change["new_price_eur_per_hour"] >= 0


@pytest.mark.asyncio
async def test_price_analytics_trends_api_structure(client, monkeypatch):
    """Trends API response structure matches UI expectations."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    response = await client.get(
        "/api/price-analytics/trends?days=30",
        headers={"X-API-Key": "dev-bypass"},
    )
    assert response.status_code == 200
    data = response.json()

    assert "count" in data
    assert "purchase_model" in data
    assert "days" in data
    assert "trends" in data
    assert isinstance(data["trends"], list)
    assert data["days"] == 30

    for trend in data["trends"]:
        assert "instance_type_id" in trend
        assert "region_id" in trend
        assert "purchase_model" in trend
        assert trend["trend_direction"] in ("increasing", "decreasing", "stable")
        assert "price_change_percent" in trend
        assert "first_price" in trend
        assert "latest_price" in trend
        assert "first_timestamp" in trend
        assert "latest_timestamp" in trend
        assert "data_points" in trend


@pytest.mark.asyncio
@pytest.mark.live_data
async def test_live_data_has_no_suspicious_changes_if_recently_ingested(client, monkeypatch):
    """When API returns changes, none exceed plausible threshold (sanity check on live data).

    Skips if no changes. This test validates that recently ingested data (after our
    connector deduplication fix) does not contain obvious product-mix artifacts.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "api_key", None)

    response = await client.get(
        "/api/price-analytics/changes?since_hours=72",
        headers={"X-API-Key": "dev-bypass"},
    )
    assert response.status_code == 200
    data = response.json()
    changes = data.get("changes", [])

    if not changes:
        pytest.skip("No price changes in last 72h - cannot validate live data quality")

    # Build PriceChange-like objects for validation
    from app.services.price_analytics import PriceChange

    pc_list = [
        PriceChange(
            instance_type_id=c["instance_type_id"],
            instance_type_name=c["instance_type_name"],
            region_id=c["region_id"],
            region_slug=c["region_slug"],
            purchase_model=PurchaseModel(c["purchase_model"]),
            old_price_eur_per_hour=c.get("old_price_eur_per_hour"),
            new_price_eur_per_hour=c.get("new_price_eur_per_hour"),
            price_change_eur_per_hour=c["price_change_eur_per_hour"],
            price_change_percent=c["price_change_percent"],
            timestamp=datetime.datetime.fromisoformat(c["timestamp"].replace("Z", "+00:00")),
        )
        for c in changes
    ]
    plausible, suspicious = validate_price_changes_data_quality(pc_list)

    assert len(suspicious) == 0, (
        f"Found {len(suspicious)} suspicious changes (possible product mix): "
        f"{[(c.instance_type_name, c.region_slug, c.price_change_percent) for c in suspicious]}"
    )
