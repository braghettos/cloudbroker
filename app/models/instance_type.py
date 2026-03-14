"""InstanceType model for compute marketplace."""

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class InstanceType(Base):
    """VM/instance type (e.g. n1-standard-4, t3.medium)."""

    __tablename__ = "instance_types"
    __table_args__ = (UniqueConstraint("provider_id", "name", name="uq_instance_type_provider_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    family: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    generation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    vcpu: Mapped[int] = mapped_column(Integer, nullable=False)
    ram_gb: Mapped[float] = mapped_column(Float, nullable=False)
    arch: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gpu_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gpu_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gpu_memory_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    disk_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    disk_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    network_performance: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cpu_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_current_gen: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    transfer_tb: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    provider: Mapped["Provider"] = relationship("Provider", back_populates="instance_types")
    prices: Mapped[list["Price"]] = relationship(
        "Price",
        back_populates="instance_type",
        cascade="all, delete-orphan",
    )
