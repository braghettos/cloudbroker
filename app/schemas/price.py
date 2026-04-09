"""Pydantic schemas for Price."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.price import PurchaseModel


class PriceBase(BaseModel):
    """Shared fields for Price."""

    purchase_model: PurchaseModel
    currency: str = Field(default="USD", min_length=3, max_length=3)
    source_currency: str | None = Field(
        None, min_length=3, max_length=3, description="Original currency before EUR conversion"
    )
    price_per_hour: float = Field(..., ge=0)


class PriceCreate(PriceBase):
    """Schema for creating a Price."""

    instance_type_id: int
    region_id: int


class PriceUpdate(BaseModel):
    """Schema for partial update of a Price."""

    purchase_model: PurchaseModel | None = None
    currency: str | None = Field(None, min_length=3, max_length=3)
    price_per_hour: float | None = Field(None, ge=0)


class PriceResponse(PriceBase):
    """Schema for Price in API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    instance_type_id: int
    region_id: int
    price_eur_per_hour: float | None = None
    effective_date: datetime | None = None
    timestamp: datetime
