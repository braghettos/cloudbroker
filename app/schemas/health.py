"""Pydantic schemas for health check responses."""

from pydantic import BaseModel, Field


class HealthCheckDetail(BaseModel):
    """Status for one subsystem check."""

    status: str = Field(..., description="ok or error")
    error: str | None = None


class HealthResponse(BaseModel):
    """Response for GET /health."""

    status: str = Field(..., description="ok, degraded, or down")
    checks: dict[str, HealthCheckDetail]
