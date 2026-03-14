"""Price model for compute marketplace."""

from __future__ import annotations

import datetime
import enum

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base


class PurchaseModel(str, enum.Enum):
    """Pricing model."""

    ON_DEMAND = "on_demand"
    SPOT = "spot"
    RESERVED = "reserved"


class Price(Base):
    """Price for an instance type in a region (time-series)."""

    __tablename__ = "prices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    instance_type_id: Mapped[int] = mapped_column(
        ForeignKey("instance_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    region_id: Mapped[int] = mapped_column(
        ForeignKey("regions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purchase_model: Mapped[PurchaseModel] = mapped_column(
        Enum(PurchaseModel, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    source_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    price_per_hour: Mapped[float] = mapped_column(Float, nullable=False)
    price_eur_per_hour: Mapped[float | None] = mapped_column(Float, nullable=True)
    effective_date: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    instance_type: Mapped["InstanceType"] = relationship(
        "InstanceType",
        back_populates="prices",
    )
    region: Mapped["Region"] = relationship("Region", back_populates="prices")
