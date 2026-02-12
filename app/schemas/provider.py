"""Pydantic schemas for Provider."""

from pydantic import BaseModel, ConfigDict, Field

from app.models.provider import ProviderType


class ProviderBase(BaseModel):
    """Shared fields for Provider."""

    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    website: str | None = Field(None, max_length=512)
    type: ProviderType = ProviderType.REGIONAL


class ProviderCreate(ProviderBase):
    """Schema for creating a Provider."""

    pass


class ProviderUpdate(BaseModel):
    """Schema for partial update of a Provider."""

    name: str | None = Field(None, min_length=1, max_length=255)
    slug: str | None = Field(None, min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    website: str | None = Field(None, max_length=512)
    type: ProviderType | None = None


class ProviderResponse(ProviderBase):
    """Schema for Provider in API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
