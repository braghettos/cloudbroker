"""Region model for compute marketplace."""

from sqlalchemy import Boolean, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Region(Base):
    """Region/datacenter of a provider."""

    __tablename__ = "regions"
    __table_args__ = (UniqueConstraint("provider_id", "slug", name="uq_region_provider_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    is_eu: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Sustainability (from batch ingestion or ENTSO-E real-time; recommendations use config/DB fallback)
    renewable_energy_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbon_intensity_g_co2_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    publishes_carbon_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    provider: Mapped["Provider"] = relationship("Provider", back_populates="regions")
    prices: Mapped[list["Price"]] = relationship(
        "Price",
        back_populates="region",
        cascade="all, delete-orphan",
    )
