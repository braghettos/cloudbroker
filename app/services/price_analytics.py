"""Price analytics service: detect price changes and trends."""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import InstanceType, Price, Region
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
) -> list[PriceChange]:
    """Detect price changes by comparing current prices with previous prices.

    Uses a window function (ROW_NUMBER) to fetch the two most recent prices
    per (instance_type, region, purchase_model) in a single query, joined
    with instance type and region names to avoid N+1.
    """
    rn = (
        func.row_number()
        .over(
            partition_by=[Price.instance_type_id, Price.region_id, Price.purchase_model],
            order_by=Price.timestamp.desc(),
        )
        .label("rn")
    )

    inner = select(
        Price.instance_type_id,
        Price.region_id,
        Price.purchase_model,
        Price.price_eur_per_hour,
        Price.timestamp,
        rn,
    )

    if purchase_model:
        inner = inner.where(Price.purchase_model == purchase_model)
    if since:
        inner = inner.where(Price.timestamp >= since)

    inner = inner.subquery()

    stmt = (
        select(
            inner.c.instance_type_id,
            inner.c.region_id,
            inner.c.purchase_model,
            inner.c.price_eur_per_hour,
            inner.c.timestamp,
            inner.c.rn,
            InstanceType.name.label("instance_type_name"),
            Region.slug.label("region_slug"),
        )
        .join(InstanceType, InstanceType.id == inner.c.instance_type_id)
        .join(Region, Region.id == inner.c.region_id)
        .where(inner.c.rn <= 2)
        .order_by(
            inner.c.instance_type_id,
            inner.c.region_id,
            inner.c.purchase_model,
            inner.c.rn,
        )
    )

    result = await db.execute(stmt)
    rows = result.all()

    grouped: dict[tuple, list] = {}
    for row in rows:
        key = (row.instance_type_id, row.region_id, row.purchase_model)
        grouped.setdefault(key, []).append(row)

    changes = []
    for (_it_id, _r_id, _pm), group in grouped.items():
        if len(group) < 2:
            continue

        latest = group[0] if group[0].rn == 1 else group[1]
        previous = group[1] if group[0].rn == 1 else group[0]

        if latest.price_eur_per_hour is None or previous.price_eur_per_hour is None:
            continue
        if abs(latest.price_eur_per_hour - previous.price_eur_per_hour) < 0.0001:
            continue

        price_change_eur = latest.price_eur_per_hour - previous.price_eur_per_hour
        price_change_percent = (price_change_eur / previous.price_eur_per_hour) * 100.0

        changes.append(
            PriceChange(
                instance_type_id=latest.instance_type_id,
                instance_type_name=latest.instance_type_name,
                region_id=latest.region_id,
                region_slug=latest.region_slug,
                purchase_model=latest.purchase_model,
                old_price_eur_per_hour=previous.price_eur_per_hour,
                new_price_eur_per_hour=latest.price_eur_per_hour,
                price_change_eur_per_hour=price_change_eur,
                price_change_percent=price_change_percent,
                timestamp=latest.timestamp,
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
) -> list[PriceTrend]:
    """Analyze price trends over a time period.

    Uses aggregate functions (MIN/MAX timestamp, FIRST/LAST price via subqueries)
    in a single query to avoid N+1.
    """
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

    first_rn = (
        func.row_number()
        .over(
            partition_by=[Price.instance_type_id, Price.region_id, Price.purchase_model],
            order_by=Price.timestamp.asc(),
        )
        .label("rn")
    )
    last_rn = (
        func.row_number()
        .over(
            partition_by=[Price.instance_type_id, Price.region_id, Price.purchase_model],
            order_by=Price.timestamp.desc(),
        )
        .label("rn_desc")
    )

    base_filter = Price.timestamp >= since
    if purchase_model:
        base_filter = (Price.timestamp >= since) & (Price.purchase_model == purchase_model)

    inner = select(
        Price.instance_type_id,
        Price.region_id,
        Price.purchase_model,
        Price.price_eur_per_hour,
        Price.timestamp,
        first_rn,
        last_rn,
    ).where(base_filter).subquery()

    count_sub = (
        select(
            inner.c.instance_type_id,
            inner.c.region_id,
            inner.c.purchase_model,
            func.count(literal_column("*")).label("data_points"),
        )
        .group_by(inner.c.instance_type_id, inner.c.region_id, inner.c.purchase_model)
        .having(func.count(literal_column("*")) >= 2)
        .subquery()
    )

    first_sub = (
        select(
            inner.c.instance_type_id,
            inner.c.region_id,
            inner.c.purchase_model,
            inner.c.price_eur_per_hour.label("first_price"),
            inner.c.timestamp.label("first_timestamp"),
        )
        .where(inner.c.rn == 1)
        .subquery()
    )

    last_sub = (
        select(
            inner.c.instance_type_id,
            inner.c.region_id,
            inner.c.purchase_model,
            inner.c.price_eur_per_hour.label("latest_price"),
            inner.c.timestamp.label("latest_timestamp"),
        )
        .where(inner.c.rn_desc == 1)
        .subquery()
    )

    stmt = (
        select(
            count_sub.c.instance_type_id,
            count_sub.c.region_id,
            count_sub.c.purchase_model,
            count_sub.c.data_points,
            first_sub.c.first_price,
            first_sub.c.first_timestamp,
            last_sub.c.latest_price,
            last_sub.c.latest_timestamp,
        )
        .join(
            first_sub,
            (count_sub.c.instance_type_id == first_sub.c.instance_type_id)
            & (count_sub.c.region_id == first_sub.c.region_id)
            & (count_sub.c.purchase_model == first_sub.c.purchase_model),
        )
        .join(
            last_sub,
            (count_sub.c.instance_type_id == last_sub.c.instance_type_id)
            & (count_sub.c.region_id == last_sub.c.region_id)
            & (count_sub.c.purchase_model == last_sub.c.purchase_model),
        )
    )

    result = await db.execute(stmt)
    rows = result.all()

    trends = []
    for row in rows:
        if row.first_price is None or row.latest_price is None:
            continue

        price_change_eur = row.latest_price - row.first_price
        price_change_percent = (price_change_eur / row.first_price) * 100.0 if row.first_price else 0.0

        if abs(price_change_percent) < 0.1:
            trend_direction = "stable"
        elif price_change_percent > 0:
            trend_direction = "increasing"
        else:
            trend_direction = "decreasing"

        trends.append(
            PriceTrend(
                instance_type_id=row.instance_type_id,
                region_id=row.region_id,
                purchase_model=row.purchase_model,
                trend_direction=trend_direction,
                price_change_percent=price_change_percent,
                price_change_eur_per_hour=price_change_eur,
                first_price=row.first_price,
                latest_price=row.latest_price,
                first_timestamp=row.first_timestamp,
                latest_timestamp=row.latest_timestamp,
                data_points=row.data_points,
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
