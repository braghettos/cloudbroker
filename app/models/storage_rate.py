"""Storage rate model for TCO (block storage per GB-month)."""

from __future__ import annotations

from sqlalchemy import Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StorageRate(Base):
    """Block storage pricing per provider/region/type for TCO calculation."""

    __tablename__ = "storage_rates"
    __table_args__ = (
        UniqueConstraint(
            "provider_slug",
            "region_slug",
            "storage_type",
            name="uq_storage_rates_provider_region_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    region_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_type: Mapped[str] = mapped_column(String(32), nullable=False)
    eur_per_gb_month: Mapped[float] = mapped_column(Float, nullable=False)
