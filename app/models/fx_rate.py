"""FX rate for currency conversion."""

import datetime

from sqlalchemy import Date, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FxRate(Base):
    """Exchange rate from one currency to another (e.g. USD -> EUR)."""

    __tablename__ = "fx_rates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    from_currency: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    to_currency: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    rate: Mapped[float] = mapped_column(Float, nullable=False)
    effective_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
