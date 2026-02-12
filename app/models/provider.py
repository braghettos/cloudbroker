"""Provider model for compute marketplace."""

import enum

from sqlalchemy import Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ProviderType(str, enum.Enum):
    """Provider classification."""

    HYPERSCALER = "hyperscaler"
    EU = "eu"
    REGIONAL = "regional"


class Provider(Base):
    """Cloud/compute provider (e.g. AWS, GCP, OVH)."""

    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    type: Mapped[ProviderType] = mapped_column(
        Enum(ProviderType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ProviderType.REGIONAL,
    )

    regions: Mapped[list["Region"]] = relationship(
        "Region",
        back_populates="provider",
        cascade="all, delete-orphan",
    )
    instance_types: Mapped[list["InstanceType"]] = relationship(
        "InstanceType",
        back_populates="provider",
        cascade="all, delete-orphan",
    )
