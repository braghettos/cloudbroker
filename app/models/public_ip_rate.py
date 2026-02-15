"""Public IP rate model for TCO."""

from __future__ import annotations

from sqlalchemy import Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PublicIpRate(Base):
    """Public IP cost per hour when allocated (per provider)."""

    __tablename__ = "public_ip_rates"
    __table_args__ = (UniqueConstraint("provider_slug", name="uq_public_ip_rates_provider_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    eur_per_hour: Mapped[float] = mapped_column(Float, nullable=False)
