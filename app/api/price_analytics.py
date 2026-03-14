"""Price analytics API endpoints."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import verify_api_key
from app.models.price import PurchaseModel
from app.schemas.price_analytics import (
    PriceChangeItem,
    PriceChangeResponse,
    PriceTrendItem,
    PriceTrendResponse,
)
from app.services.price_analytics import analyze_price_trends, detect_price_changes

router = APIRouter(prefix="/price-analytics", tags=["price-analytics"])


@router.get("/changes", response_model=PriceChangeResponse)
async def get_price_changes(
    purchase_model: PurchaseModel | None = Query(None, description="Filter by purchase model"),
    since_hours: int = Query(24, ge=1, le=8760, description="Only check prices updated in the last N hours"),
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(verify_api_key),
) -> PriceChangeResponse:
    """Get price changes detected in the system."""
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    changes = await detect_price_changes(db, purchase_model=purchase_model, since=since)

    return PriceChangeResponse(
        count=len(changes),
        purchase_model=purchase_model.value if purchase_model else None,
        since_hours=since_hours,
        changes=[
            PriceChangeItem(
                instance_type_id=c.instance_type_id,
                instance_type_name=c.instance_type_name,
                region_id=c.region_id,
                region_slug=c.region_slug,
                purchase_model=c.purchase_model,
                old_price_eur_per_hour=c.old_price_eur_per_hour,
                new_price_eur_per_hour=c.new_price_eur_per_hour,
                price_change_eur_per_hour=c.price_change_eur_per_hour,
                price_change_percent=c.price_change_percent,
                timestamp=c.timestamp,
            )
            for c in changes
        ],
    )


@router.get("/trends", response_model=PriceTrendResponse)
async def get_price_trends(
    purchase_model: PurchaseModel | None = Query(None, description="Filter by purchase model"),
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(verify_api_key),
) -> PriceTrendResponse:
    """Get price trend analysis over a time period."""
    trends = await analyze_price_trends(db, purchase_model=purchase_model, days=days)

    return PriceTrendResponse(
        count=len(trends),
        purchase_model=purchase_model.value if purchase_model else None,
        days=days,
        trends=[
            PriceTrendItem(
                instance_type_id=t.instance_type_id,
                region_id=t.region_id,
                purchase_model=t.purchase_model,
                trend_direction=t.trend_direction,
                price_change_percent=t.price_change_percent,
                price_change_eur_per_hour=t.price_change_eur_per_hour,
                first_price=t.first_price,
                latest_price=t.latest_price,
                first_timestamp=t.first_timestamp,
                latest_timestamp=t.latest_timestamp,
                data_points=t.data_points,
            )
            for t in trends
        ],
    )
