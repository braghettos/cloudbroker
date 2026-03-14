"""Pydantic schemas for Region."""

from pydantic import BaseModel, ConfigDict, Field


class RegionBase(BaseModel):
    """Shared fields for Region."""

    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    country: str | None = Field(None, max_length=2)
    is_eu: bool = False
    renewable_energy_pct: float | None = Field(None, ge=0, le=1, description="Renewable energy share 0-1")
    carbon_intensity_g_co2_kwh: float | None = Field(
        None, ge=0, description="Grid carbon intensity gCO2eq/kWh"
    )
    publishes_carbon_data: bool = False


class RegionCreate(RegionBase):
    """Schema for creating a Region."""

    provider_id: int


class RegionUpdate(BaseModel):
    """Schema for partial update of a Region."""

    name: str | None = Field(None, min_length=1, max_length=255)
    slug: str | None = Field(None, min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    country: str | None = Field(None, max_length=2)
    is_eu: bool | None = None
    renewable_energy_pct: float | None = Field(None, ge=0, le=1)
    carbon_intensity_g_co2_kwh: float | None = Field(None, ge=0)
    publishes_carbon_data: bool | None = None


class RegionResponse(RegionBase):
    """Schema for Region in API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int
