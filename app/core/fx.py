"""FX conversion and EUR normalization."""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FxRate

# Fallback rate when no fx_rates row exists (1 USD = 0.92 EUR). Use ingest or load real FX data.
DEFAULT_USD_TO_EUR = 0.92


async def get_rate_to_eur(db: AsyncSession, from_currency: str, on_date: datetime.date | None = None) -> float:
    """Return rate to convert 1 unit of from_currency to EUR. Uses latest fx_rates row or in-memory fallback when empty."""
    if from_currency.upper() == "EUR":
        return 1.0
    on_date = on_date or datetime.date.today()
    stmt = (
        select(FxRate.rate)
        .where(FxRate.from_currency == from_currency.upper())
        .where(FxRate.to_currency == "EUR")
        .where(FxRate.effective_date <= on_date)
        .order_by(FxRate.effective_date.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    row = result.scalars().first()
    if row is not None:
        return float(row)
    if from_currency.upper() == "USD":
        return DEFAULT_USD_TO_EUR
    return DEFAULT_USD_TO_EUR  # fallback for any other currency when no fx_rates in DB


def to_eur(amount: float, rate: float) -> float:
    """Convert amount to EUR using rate (1 unit from_currency = rate EUR)."""
    return round(float(Decimal(str(amount)) * Decimal(str(rate))), 6)
