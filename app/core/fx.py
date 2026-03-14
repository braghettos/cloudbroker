"""FX conversion and EUR normalization.

Real-time rates from the European Central Bank (ECB) daily XML feed.
Source: https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml
  - Free, no authentication required
  - Updated daily at ~16:00 CET
  - Covers ~30 major currencies (USD, CHF, SEK, GBP, JPY, CAD, AUD, etc.)

Rates are cached in-memory for 6 hours to avoid hitting ECB repeatedly per request.
DB-stored rates (fx_rates table) are used when the ECB feed is unavailable.
Hardcoded fallback used only when both ECB and DB are unavailable.
"""

from __future__ import annotations

import asyncio
import datetime
import time
import xml.etree.ElementTree as ET
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import FxRate

logger = get_logger(__name__)

# Hardcoded fallbacks (used only when ECB feed AND DB are both unavailable)
_FALLBACK_RATES: dict[str, float] = {
    "USD": 0.92,
    "CHF": 1.05,
    "SEK": 0.088,
    "NOK": 0.086,
    "DKK": 0.134,
    "GBP": 1.17,
    "PLN": 0.24,
    "CZK": 0.040,
    "HUF": 0.0026,
    "RON": 0.20,
    "BGN": 0.51,
    "HRK": 0.133,
    "CAD": 0.68,
    "AUD": 0.60,
    "NZD": 0.55,
    "JPY": 0.0062,
    "SGD": 0.68,
    "HKD": 0.118,
    "KRW": 0.00067,
    "INR": 0.011,
    "BRL": 0.17,
    "MXN": 0.045,
    "ZAR": 0.050,
    "TRY": 0.027,
    "AED": 0.25,
}

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_CACHE_TTL_SECONDS = 6 * 3600  # 6 hours

# In-process cache: currency → rate, fetched_at
_rate_cache: dict[str, float] = {}
_cache_fetched_at: float = 0.0
_cache_lock = asyncio.Lock()


def _is_cache_fresh() -> bool:
    return _cache_fetched_at > 0 and (time.time() - _cache_fetched_at) < _CACHE_TTL_SECONDS


async def _fetch_ecb_rates() -> dict[str, float]:
    """Fetch latest EUR FX rates from ECB daily XML feed. Returns {currency: rate_to_eur}."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(ECB_URL)
            r.raise_for_status()
            text = r.text
    except Exception as e:
        logger.warning("ECB FX feed fetch failed", error=str(e))
        return {}

    try:
        root = ET.fromstring(text)
        # ECB XML: <gesmes:Envelope><Cube><Cube time="..."><Cube currency="USD" rate="1.08"/>...
        rates: dict[str, float] = {}
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "Cube" and "currency" in elem.attrib and "rate" in elem.attrib:
                currency = elem.attrib["currency"].upper()
                rate_eur_to_foreign = float(elem.attrib["rate"])
                # ECB rates are foreign/EUR; invert to get EUR/foreign
                if rate_eur_to_foreign > 0:
                    rates[currency] = 1.0 / rate_eur_to_foreign
        logger.info("ECB FX rates loaded", currencies=len(rates))
        return rates
    except Exception as e:
        logger.warning("ECB FX XML parse failed", error=str(e))
        return {}


async def _ensure_rates_cached() -> None:
    """Populate the in-memory rate cache from ECB (if stale)."""
    global _cache_fetched_at
    async with _cache_lock:
        if _is_cache_fresh():
            return
        rates = await _fetch_ecb_rates()
        if rates:
            _rate_cache.clear()
            _rate_cache.update(rates)
            _cache_fetched_at = time.time()


async def get_rate_to_eur(db: AsyncSession, from_currency: str, on_date: datetime.date | None = None) -> float:
    """Return rate to convert 1 unit of from_currency to EUR.

    Priority:
      1. ECB daily feed (real-time, free, no auth) — cached 6h in-process
      2. DB fx_rates table (populated by FX ingestion if configured)
      3. Hardcoded fallback (last resort, may be stale)
    """
    if from_currency.upper() == "EUR":
        return 1.0

    currency = from_currency.upper()

    # 1. ECB in-process cache
    await _ensure_rates_cached()
    if currency in _rate_cache:
        return _rate_cache[currency]

    # 2. DB fallback
    on_date = on_date or datetime.date.today()
    stmt = (
        select(FxRate.rate)
        .where(FxRate.from_currency == currency)
        .where(FxRate.to_currency == "EUR")
        .where(FxRate.effective_date <= on_date)
        .order_by(FxRate.effective_date.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    row = result.scalars().first()
    if row is not None:
        return float(row)

    # 3. Hardcoded fallback
    fallback = _FALLBACK_RATES.get(currency)
    if fallback is not None:
        logger.debug("Using hardcoded FX fallback", currency=currency, rate=fallback)
        return fallback

    logger.warning("No FX rate found for currency; using 1:1", currency=currency)
    return 1.0


def to_eur(amount: float, rate: float) -> float:
    """Convert amount to EUR using rate (1 unit from_currency = rate EUR)."""
    return round(float(Decimal(str(amount)) * Decimal(str(rate))), 6)
