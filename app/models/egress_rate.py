"""Egress rate model for TCO-aware recommendations."""

from __future__ import annotations

from sqlalchemy import Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EgressRate(Base):
    """Egress (data transfer out) pricing per provider for TCO calculation."""

    __tablename__ = "egress_rates"
    __table_args__ = (UniqueConstraint("provider_slug", name="uq_egress_rates_provider_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    egress_eur_per_gb: Mapped[float] = mapped_column(Float, nullable=False)
    free_tier_gb_per_month: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cross_region_eur_per_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    cross_az_eur_per_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
