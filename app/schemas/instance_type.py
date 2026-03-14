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
    gpu_count: int | None = Field(None, ge=0, description="Number of GPUs, if any")
    gpu_model: str | None = Field(None, max_length=64, description="GPU model name (e.g. nvidia-t4, nvidia-a100)")
    gpu_memory_gb: float | None = Field(None, gt=0, description="Total GPU memory in GB")
    disk_gb: float | None = Field(None, ge=0, description="Included local/root disk in GB")
    disk_type: str | None = Field(None, max_length=32, description="Disk type: local_ssd, local_hdd, network")
    network_performance: str | None = Field(None, max_length=64, description="Network performance tier (e.g. Up to 25 Gigabit)")
    cpu_type: str | None = Field(None, max_length=32, description="CPU sharing model: shared or dedicated")
    is_current_gen: bool | None = Field(None, description="Whether this is a current-generation instance type")
    transfer_tb: float | None = Field(None, ge=0, description="Included monthly data transfer in TB")
    is_available: bool = Field(default=True, description="Whether the instance type is currently orderable")


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
    gpu_count: int | None = Field(None, ge=0)
    gpu_model: str | None = Field(None, max_length=64)
    gpu_memory_gb: float | None = Field(None, gt=0)
    disk_gb: float | None = Field(None, ge=0)
    disk_type: str | None = Field(None, max_length=32)
    network_performance: str | None = Field(None, max_length=64)
    cpu_type: str | None = Field(None, max_length=32)
    is_current_gen: bool | None = None
    transfer_tb: float | None = Field(None, ge=0)
    is_available: bool | None = None


class InstanceTypeResponse(InstanceTypeBase):
    """Schema for InstanceType in API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int
    metadata_json: str | None = None
