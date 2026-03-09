"""Price analytics service: detect price changes and trends."""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import TEST_PROVIDER_PREFIX
from app.core.logging import get_logger
from app.models import InstanceType, Price, Provider, Region
from app.models.price import PurchaseModel

logger = get_logger(__name__)


@dataclass
class PriceChange:
    """Represents a price change event."""

    instance_type_id: int
    instance_type_name: str
    region_id: int
    region_slug: str
    purchase_model: PurchaseModel
    old_price_eur_per_hour: float | None
    new_price_eur_per_hour: float | None
    price_change_eur_per_hour: float
    price_change_percent: float
    timestamp: datetime.datetime


@dataclass
class PriceTrend:
    """Represents price trend data."""

    instance_type_id: int
    region_id: int
    purchase_model: PurchaseModel
    trend_direction: str  # "increasing", "decreasing", "stable"
    price_change_percent: float
    price_change_eur_per_hour: float
    first_price: float | None
    latest_price: float | None
    first_timestamp: datetime.datetime
    latest_timestamp: datetime.datetime
    data_points: int


async def detect_price_changes(
    db: AsyncSession,
    purchase_model: PurchaseModel | None = None,
    since: datetime.datetime | None = None,
    exclude_test_providers: bool = True,
) -> list[PriceChange]:
    """Detect price changes by comparing current prices with previous prices.

    Args:
        db: Database session
        purchase_model: Filter by purchase model (None = all)
        since: Only check prices updated since this timestamp (None = check all)
        exclude_test_providers: If True, exclude providers with slug starting with test prefix (default: True)

    Returns:
        List of price change events
    """
    changes = []

    # Get all unique combinations
    stmt = select(
        Price.instance_type_id,
        Price.region_id,
        Price.purchase_model,
    ).distinct()

    if exclude_test_providers:
        stmt = (
            stmt.select_from(Price)
            .join(InstanceType, Price.instance_type_id == InstanceType.id)
            .join(Provider, InstanceType.provider_id == Provider.id)
            .where(~Provider.slug.like(f"{TEST_PROVIDER_PREFIX}%"))
        )

    if purchase_model:
        stmt = stmt.where(Price.purchase_model == purchase_model)

    combinations = await db.execute(stmt)

    for combo in combinations:
        instance_type_id, region_id, purchase_model_val = combo

        # Get two most recent prices for this combination
        prices_stmt = (
            select(Price.price_eur_per_hour, Price.timestamp)
            .where(Price.instance_type_id == instance_type_id)
            .where(Price.region_id == region_id)
            .where(Price.purchase_model == purchase_model_val)
            .order_by(Price.timestamp.desc())
            .limit(2)
        )

        if since:
            prices_stmt = prices_stmt.where(Price.timestamp >= since)

        prices_result = await db.execute(prices_stmt)
        prices = prices_result.all()

        if len(prices) < 2:
            # Need at least 2 data points to detect a change
            continue

        latest_price, latest_ts = prices[0]
        previous_price, previous_ts = prices[1]

        # Skip if prices are the same (within small tolerance for floating point)
        if latest_price is None or previous_price is None:
            continue

        if abs(latest_price - previous_price) < 0.0001:  # Less than 0.0001 EUR difference
            continue

        # Calculate change
        price_change_eur = latest_price - previous_price
        price_change_percent = (price_change_eur / previous_price) * 100.0

        # Get instance type and region names
        it_result = await db.execute(select(InstanceType.name).where(InstanceType.id == instance_type_id))
        instance_type_name = it_result.scalar_one_or_none() or f"ID:{instance_type_id}"

        region_result = await db.execute(select(Region.slug).where(Region.id == region_id))
        region_slug = region_result.scalar_one_or_none() or f"ID:{region_id}"

        changes.append(
            PriceChange(
                instance_type_id=instance_type_id,
                instance_type_name=instance_type_name,
                region_id=region_id,
                region_slug=region_slug,
                purchase_model=purchase_model_val,
                old_price_eur_per_hour=previous_price,
                new_price_eur_per_hour=latest_price,
                price_change_eur_per_hour=price_change_eur,
                price_change_percent=price_change_percent,
                timestamp=latest_ts,
            )
        )

    logger.info(
        "Detected price changes",
        count=len(changes),
        purchase_model=purchase_model.value if purchase_model else None,
    )

    return changes


async def analyze_price_trends(
    db: AsyncSession,
    purchase_model: PurchaseModel | None = None,
    days: int = 30,
    exclude_test_providers: bool = True,
) -> list[PriceTrend]:
    """Analyze price trends over a time period.

    Args:
        db: Database session
        purchase_model: Filter by purchase model (None = all)
        days: Number of days to analyze
        exclude_test_providers: If True, exclude providers with slug starting with test prefix (default: True)

    Returns:
        List of price trend analyses
    """
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

    trends = []

    # Get all unique combinations
    stmt = select(
        Price.instance_type_id,
        Price.region_id,
        Price.purchase_model,
    ).distinct()

    if exclude_test_providers:
        stmt = (
            stmt.select_from(Price)
            .join(InstanceType, Price.instance_type_id == InstanceType.id)
            .join(Provider, InstanceType.provider_id == Provider.id)
            .where(~Provider.slug.like(f"{TEST_PROVIDER_PREFIX}%"))
        )

    if purchase_model:
        stmt = stmt.where(Price.purchase_model == purchase_model)

    combinations = await db.execute(stmt)

    for combo in combinations:
        instance_type_id, region_id, purchase_model = combo

        # Get all prices for this combination within the time period
        prices_result = await db.execute(
            select(Price.price_eur_per_hour, Price.timestamp)
            .where(Price.instance_type_id == instance_type_id)
            .where(Price.region_id == region_id)
            .where(Price.purchase_model == purchase_model)
            .where(Price.timestamp >= since)
            .order_by(Price.timestamp.asc())
        )

        prices = prices_result.all()

        if len(prices) < 2:
            # Need at least 2 data points for trend analysis
            continue

        first_price, first_ts = prices[0]
        latest_price, latest_ts = prices[-1]

        if first_price is None or latest_price is None:
            continue

        price_change_eur = latest_price - first_price
        price_change_percent = (price_change_eur / first_price) * 100.0

        # Determine trend direction
        if abs(price_change_percent) < 0.1:  # Less than 0.1% change = stable
            trend_direction = "stable"
        elif price_change_percent > 0:
            trend_direction = "increasing"
        else:
            trend_direction = "decreasing"

        trends.append(
            PriceTrend(
                instance_type_id=instance_type_id,
                region_id=region_id,
                purchase_model=purchase_model,
                trend_direction=trend_direction,
                price_change_percent=price_change_percent,
                price_change_eur_per_hour=price_change_eur,
                first_price=first_price,
                latest_price=latest_price,
                first_timestamp=first_ts,
                latest_timestamp=latest_ts,
                data_points=len(prices),
            )
        )

    logger.info(
        "Analyzed price trends",
        count=len(trends),
        days=days,
        purchase_model=purchase_model.value if purchase_model else None,
    )

    return trends


# Threshold above which a percent change is suspicious (likely product mix, e.g. spot vs on-demand)
SUSPICIOUS_CHANGE_PERCENT = 500.0


def validate_price_changes_data_quality(
    changes: list[PriceChange],
    max_plausible_percent: float = SUSPICIOUS_CHANGE_PERCENT,
) -> tuple[list[PriceChange], list[PriceChange]]:
    """Split changes into plausible (real) vs suspicious (possible product mix).

    Large percent changes (e.g. >500%) often indicate comparing different products
    (Linux vs Windows, spot vs on-demand) rather than real price changes.

    Returns:
        (plausible_changes, suspicious_changes)
    """
    plausible: list[PriceChange] = []
    suspicious: list[PriceChange] = []
    for c in changes:
        if c.old_price_eur_per_hour is None or c.old_price_eur_per_hour <= 0:
            suspicious.append(c)
            continue
        if abs(c.price_change_percent) > max_plausible_percent:
            suspicious.append(c)
        else:
            plausible.append(c)
    return (plausible, suspicious)
