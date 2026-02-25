"""OS license rate model for TCO (Windows, RHEL surcharges)."""

from __future__ import annotations

from sqlalchemy import Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OsLicenseRate(Base):
    """OS surcharge per vCPU-hour for licensed images."""

    __tablename__ = "os_license_rates"
    __table_args__ = (UniqueConstraint("provider_slug", "os_type", name="uq_os_license_rates_provider_os"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    os_type: Mapped[str] = mapped_column(String(32), nullable=False)
    eur_per_vcpu_hour: Mapped[float] = mapped_column(Float, nullable=False)
