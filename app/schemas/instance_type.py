"""Pydantic schemas for InstanceType."""

from pydantic import BaseModel, ConfigDict, Field


class InstanceTypeBase(BaseModel):
    """Shared fields for InstanceType."""

    name: str = Field(..., min_length=1, max_length=128)
    family: str | None = Field(None, max_length=64)
    generation: str | None = Field(None, max_length=32)
    vcpu: int = Field(..., ge=1)
    ram_gb: float = Field(..., gt=0)
    arch: str | None = Field(None, max_length=32)


class InstanceTypeCreate(InstanceTypeBase):
    """Schema for creating an InstanceType."""

    provider_id: int


class InstanceTypeUpdate(BaseModel):
    """Schema for partial update of an InstanceType."""

    name: str | None = Field(None, min_length=1, max_length=128)
    family: str | None = Field(None, max_length=64)
    generation: str | None = Field(None, max_length=32)
    vcpu: int | None = Field(None, ge=1)
    ram_gb: float | None = Field(None, gt=0)
    arch: str | None = Field(None, max_length=32)


class InstanceTypeResponse(InstanceTypeBase):
    """Schema for InstanceType in API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int
