"""Pydantic schemas for price analytics responses."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.price import PurchaseModel


class PriceChangeItem(BaseModel):
    """A single price change event."""

    instance_type_id: int
    instance_type_name: str
    region_id: int
    region_slug: str
    purchase_model: PurchaseModel
    old_price_eur_per_hour: float | None
    new_price_eur_per_hour: float | None
    price_change_eur_per_hour: float
    price_change_percent: float
    timestamp: datetime


class PriceChangeResponse(BaseModel):
    """Response for GET /price-analytics/changes."""

    count: int
    purchase_model: str | None
    since_hours: int
    changes: list[PriceChangeItem]


class PriceTrendItem(BaseModel):
    """A single price trend analysis."""

    instance_type_id: int
    region_id: int
    purchase_model: PurchaseModel
    trend_direction: str = Field(..., description="increasing, decreasing, or stable")
    price_change_percent: float
    price_change_eur_per_hour: float
    first_price: float | None
    latest_price: float | None
    first_timestamp: datetime
    latest_timestamp: datetime
    data_points: int


class PriceTrendResponse(BaseModel):
    """Response for GET /price-analytics/trends."""

    count: int
    purchase_model: str | None
    days: int
    trends: list[PriceTrendItem]
