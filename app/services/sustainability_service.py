"""Real-time and static sustainability data for cloud regions.

Priority chain per region:
  1. Config override (manual, highest priority)
  2. Real-time API:
     - EU regions  → ENTSO-E (free, requires token)
     - UK regions  → UK Carbon Intensity API (free, no auth)
     - US regions  → EIA OpenData API (free, requires API key)
  3. Static country-level fallback (Ember Climate yearly averages, embedded)
  4. DB fallback (caller's responsibility)

Cache: bounded TTL dict with asyncio.Lock (safe for multi-worker/async environments).
"""

from __future__ import annotations

import asyncio
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

ENTSOE_BASE = "https://web-api.tp.entsoe.eu/api"
UK_CARBON_API_BASE = "https://api.carbonintensity.org.uk"
EIA_API_BASE = "https://api.eia.gov/v2"

# ENTSO-E psrType → direct emission factor (gCO2eq/kWh, IPCC AR6 lifecycle values)
# Renewable/low-carbon sources: 0 for scoring (we use renewable_pct separately)
ENTSOE_EMISSION_FACTORS: dict[str, float] = {
    "B01": 230,    # Biomass (lifecycle, varies widely but median ~230 gCO2/kWh)
    "B02": 820,    # Fossil Brown coal/Lignite
    "B03": 440,    # Fossil Coal-derived gas
    "B04": 410,    # Fossil Gas (combined cycle CCGT)
    "B05": 820,    # Fossil Hard coal
    "B06": 650,    # Fossil Oil
    "B07": 800,    # Fossil Oil shale
    "B08": 380,    # Fossil Peat
    "B09": 38,     # Geothermal
    "B10": 24,     # Hydro Pumped Storage (lifecycle incl. upstream)
    "B11": 4,      # Hydro Run-of-river
    "B12": 4,      # Hydro Water Reservoir
    "B13": 8,      # Marine
    "B14": 12,     # Nuclear
    "B15": 25,     # Other renewable
    "B16": 5,      # Solar (lifecycle)
    "B17": 330,    # Waste (mixed, partially biogenic)
    "B18": 12,     # Wind Offshore (lifecycle)
    "B19": 11,     # Wind Onshore (lifecycle)
    "B20": 400,    # Other (unknown → assume fossil mix average)
}

# psrTypes that count as renewable for renewable_pct calculation
ENTSOE_RENEWABLE_PSRTYPES = frozenset({
    "B01",  # Biomass
    "B09",  # Geothermal
    "B10",  # Hydro Pumped Storage
    "B11",  # Hydro Run-of-river
    "B12",  # Hydro Water Reservoir
    "B13",  # Marine
    "B15",  # Other renewable
    "B16",  # Solar
    "B18",  # Wind Offshore
    "B19",  # Wind Onshore
})

# Low-carbon psrTypes (renewable + nuclear) — used for broader "clean energy" metric
ENTSOE_LOW_CARBON_PSRTYPES = ENTSOE_RENEWABLE_PSRTYPES | frozenset({"B14"})

# ---------------------------------------------------------------------------
# Static country-level carbon intensity fallback (Ember Climate 2024 averages)
# Source: https://ember-climate.org/data/data-tools/data-explorer/ (free download)
# Values: (carbon_intensity_g_co2_kwh, renewable_energy_pct 0-1)
# Used when no real-time API or ENTSO-E mapping is available for a country.
# ---------------------------------------------------------------------------
COUNTRY_STATIC_INTENSITY: dict[str, tuple[float, float]] = {
    # Europe (EU / EEA — fallback when ENTSO-E zone is not mapped)
    "AT": (126, 0.82),   # Austria
    "BE": (148, 0.43),   # Belgium
    "BG": (395, 0.29),   # Bulgaria
    "CH": (32, 0.72),    # Switzerland (nuclear + hydro)
    "CY": (570, 0.22),   # Cyprus
    "CZ": (385, 0.26),   # Czechia
    "DE": (335, 0.59),   # Germany
    "DK": (135, 0.84),   # Denmark
    "EE": (283, 0.42),   # Estonia
    "ES": (170, 0.60),   # Spain
    "FI": (87,  0.73),   # Finland
    "FR": (56,  0.82),   # France (nuclear)
    "GB": (215, 0.50),   # United Kingdom
    "GR": (295, 0.47),   # Greece
    "HR": (160, 0.56),   # Croatia
    "HU": (222, 0.47),   # Hungary
    "IE": (283, 0.42),   # Ireland
    "IT": (328, 0.44),   # Italy
    "LT": (180, 0.63),   # Lithuania
    "LU": (97,  0.62),   # Luxembourg
    "LV": (130, 0.64),   # Latvia
    "MT": (510, 0.12),   # Malta
    "NL": (263, 0.43),   # Netherlands
    "NO": (26,  0.98),   # Norway (almost all hydro)
    "PL": (641, 0.26),   # Poland
    "PT": (120, 0.73),   # Portugal
    "RO": (263, 0.44),   # Romania
    "SE": (41,  0.82),   # Sweden (hydro + nuclear)
    "SI": (220, 0.45),   # Slovenia
    "SK": (140, 0.55),   # Slovakia
    # Non-EU
    "US": (386, 0.24),   # United States (national average; EIA 2024)
    "CA": (130, 0.67),   # Canada
    "AU": (455, 0.33),   # Australia
    "JP": (458, 0.23),   # Japan
    "SG": (368, 0.04),   # Singapore
    "KR": (415, 0.09),   # South Korea
    "IN": (680, 0.23),   # India
    "BR": (95,  0.86),   # Brazil
    "ZA": (760, 0.09),   # South Africa
    "HK": (680, 0.04),   # Hong Kong
    "TW": (545, 0.08),   # Taiwan
    "ID": (760, 0.14),   # Indonesia
    "TH": (480, 0.17),   # Thailand
    "IL": (510, 0.10),   # Israel
    "AE": (552, 0.06),   # UAE
    "SA": (696, 0.02),   # Saudi Arabia
    "NZ": (120, 0.84),   # New Zealand
    "MX": (420, 0.25),   # Mexico
    "AR": (340, 0.35),   # Argentina
    "CL": (275, 0.53),   # Chile
}

# Map country code → EIA region/subregion for real-time lookup
# EIA v2 electricity/rto/region-data/data uses "respondent" codes:
# ERCO=Texas, MISO=Midwest, PJM=Mid-Atlantic/Southeast, NYIS=New York,
# ISNE=New England, CISO=California, NWPP=Northwest, SWPP=Southwest
# We store representative EIA respondent codes per AWS/Azure/DO region slug
EIA_REGION_MAP: dict[str, str] = {
    # AWS
    "us-east-1": "PJM",       # N. Virginia → PJM grid
    "us-east-2": "MISO",      # Ohio → MISO grid
    "us-west-1": "CISO",      # N. California → CAISO
    "us-west-2": "NWPP",      # Oregon → Northwest Power Pool
    "ca-central-1": "",        # Canada — no EIA data
    "ap-northeast-1": "",      # Tokyo — no EIA
    "ap-southeast-1": "",      # Singapore — no EIA
    # Azure
    "eastus": "PJM",
    "eastus2": "PJM",
    "westus2": "NWPP",
    "westus3": "CISO",
    "centralus": "MISO",
    # DigitalOcean
    "nyc1": "NYIS",
    "nyc2": "NYIS",
    "nyc3": "NYIS",
    "sfo2": "CISO",
    "sfo3": "CISO",
}

# EIA generation type codes → emission factor (gCO2/kWh, EPA eGRID lifecycle)
EIA_FUEL_EMISSION_FACTORS: dict[str, float] = {
    "COL": 1001,   # Coal
    "NG":  440,    # Natural gas (CCGT)
    "OIL": 650,    # Oil/petroleum
    "NUC": 12,     # Nuclear
    "WAT": 4,      # Hydro
    "WND": 11,     # Wind
    "SUN": 5,      # Solar
    "GEO": 38,     # Geothermal
    "BIO": 230,    # Biomass/biofuel
    "OTH": 400,    # Other
}
EIA_RENEWABLE_FUELS = frozenset({"WAT", "WND", "SUN", "GEO", "BIO"})


# ---------------------------------------------------------------------------
# Bounded TTL cache (asyncio-safe)
# ---------------------------------------------------------------------------

class _TTLCache:
    """Bounded TTL dict protected by asyncio.Lock.

    - max_entries: evicts oldest entry when exceeded (LRU-lite: FIFO eviction)
    - ttl_seconds: entries expire after this many seconds
    Safe for concurrent asyncio tasks (NOT for multi-process, which requires Redis/shared memory).
    """

    def __init__(self, ttl_seconds: int, max_entries: int) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._lock = asyncio.Lock()
        self._store: dict[tuple, tuple[object, float]] = {}  # key -> (value, fetched_at)

    async def get(self, key: tuple) -> object | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, fetched_at = entry
            if (time.time() - fetched_at) >= self._ttl:
                del self._store[key]
                return None
            return value

    async def set(self, key: tuple, value: object) -> None:
        async with self._lock:
            if len(self._store) >= self._max and key not in self._store:
                # Evict oldest entry
                oldest_key = next(iter(self._store))
                del self._store[oldest_key]
            self._store[key] = (value, time.time())

    async def invalidate(self, key: tuple) -> None:
        async with self._lock:
            self._store.pop(key, None)


@dataclass
class SustainabilityData:
    """Current sustainability metrics for a region."""

    carbon_intensity_g_co2_kwh: float
    renewable_energy_pct: float   # 0-1
    source: str = "unknown"       # "entsoe", "uk_api", "eia", "static", "config", "db"


# Module-level cache instance (one per process; safe for async workers)
_cache: _TTLCache | None = None
_config: dict | None = None
_config_lock = asyncio.Lock()


def _get_cache() -> _TTLCache:
    global _cache
    if _cache is None:
        _cache = _TTLCache(
            ttl_seconds=settings.sustainability_cache_ttl_seconds,
            max_entries=settings.sustainability_cache_max_entries,
        )
    return _cache


def _load_sustainability_config() -> dict:
    """Load config from config/sustainability.yaml."""
    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "sustainability.yaml"
    if not config_path.exists():
        return {"region_overrides": {}, "region_to_entsoe_area": {}}
    try:
        try:
            import yaml
        except ImportError:
            return {"region_overrides": {}, "region_to_entsoe_area": {}}
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        return {
            "region_overrides": data.get("region_overrides") or {},
            "region_to_entsoe_area": data.get("region_to_entsoe_area") or {},
        }
    except Exception as e:
        logger.warning("Failed to load sustainability config", path=str(config_path), error=str(e))
        return {"region_overrides": {}, "region_to_entsoe_area": {}}


async def _get_config() -> dict:
    global _config
    async with _config_lock:
        if _config is None:
            _config = _load_sustainability_config()
    return _config


def _get_region_override(cfg: dict, provider_slug: str, region_slug: str) -> SustainabilityData | None:
    """Return manual override if configured."""
    prov = (cfg.get("region_overrides") or {}).get(provider_slug)
    if not prov:
        return None
    data = prov.get(region_slug)
    if not data:
        return None
    return SustainabilityData(
        carbon_intensity_g_co2_kwh=float(data.get("carbon_intensity_g_co2_kwh", 0)),
        renewable_energy_pct=float(data.get("renewable_energy_pct", 0)),
        source="config",
    )


def _get_entsoe_area(cfg: dict, provider_slug: str, region_slug: str) -> str | None:
    """Return ENTSO-E bidding zone EIC code if configured."""
    mapping = cfg.get("region_to_entsoe_area") or {}
    return (mapping.get(provider_slug) or {}).get(region_slug)


# ---------------------------------------------------------------------------
# ENTSO-E (EU grid — free, token required)
# ---------------------------------------------------------------------------

async def _fetch_from_entsoe(token: str, area_code: str) -> SustainabilityData | None:
    """Fetch actual generation from ENTSO-E A75, compute carbon intensity and renewable %."""
    now = datetime.now(timezone.utc)
    end = now.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=48)
    params = {
        "documentType": "A75",
        "processType": "A16",
        "in_Domain": area_code,
        "periodStart": start.strftime("%Y%m%d%H00"),
        "periodEnd": end.strftime("%Y%m%d%H00"),
        "securityToken": token,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(ENTSOE_BASE, params=params)
            r.raise_for_status()
            text = r.text
    except Exception as e:
        logger.warning("ENTSO-E fetch failed", area=area_code, error=str(e))
        return None

    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        logger.warning("ENTSO-E XML parse failed", error=str(e))
        return None

    def local(tag: str) -> str:
        return tag.split("}")[-1] if "}" in tag else tag

    series_data: list[tuple[str, dict[int, float]]] = []
    max_position = 0

    for ts in root.iter():
        if local(ts.tag) != "TimeSeries":
            continue
        psr_elem = next((c for c in ts if local(c.tag) == "MktPSRType"), None)
        if psr_elem is None:
            continue
        psr_type = next(
            ((c.text or "").strip() for c in psr_elem if local(c.tag) == "psrType"), None
        )
        if not psr_type:
            continue
        for period in ts.iter():
            if local(period.tag) != "Period":
                continue
            pos_to_q: dict[int, float] = {}
            for pt in period.iter():
                if local(pt.tag) == "Point":
                    pos = q = None
                    for ch in pt:
                        if local(ch.tag) == "position":
                            try:
                                pos = int(ch.text or 0)
                            except ValueError:
                                pos = 0
                        elif local(ch.tag) == "quantity":
                            try:
                                q = float(ch.text or 0)
                            except ValueError:
                                q = 0.0
                    if pos is not None and q is not None:
                        pos_to_q[pos] = q
                        max_position = max(max_position, pos)
            if pos_to_q:
                series_data.append((psr_type, pos_to_q))
            break

    if not series_data or max_position < 1:
        return None

    latest_points = [
        (psr, pos_map.get(max_position, 0.0))
        for psr, pos_map in series_data
    ]
    latest_points = [(psr, q) for psr, q in latest_points if q > 0]
    if not latest_points:
        return None

    total_mw = sum(q for _, q in latest_points)
    if total_mw <= 0:
        return None

    carbon_sum = sum(q * ENTSOE_EMISSION_FACTORS.get(psr, 400) for psr, q in latest_points)
    renewable_mw = sum(q for psr, q in latest_points if psr in ENTSOE_RENEWABLE_PSRTYPES)

    return SustainabilityData(
        carbon_intensity_g_co2_kwh=carbon_sum / total_mw,
        renewable_energy_pct=renewable_mw / total_mw,
        source="entsoe",
    )


# ---------------------------------------------------------------------------
# UK Carbon Intensity API (free, no auth, real-time)
# https://api.carbonintensity.org.uk — National Grid ESO official data
# ---------------------------------------------------------------------------

# UK regions that should use this API
_UK_REGION_SLUGS = frozenset({
    # AWS
    "eu-west-2",
    # Azure
    "uksouth", "ukwest",
    # DigitalOcean
    "lon1",
    # Hetzner (no UK DC but slug guard)
})


async def _fetch_from_uk_carbon_api() -> SustainabilityData | None:
    """Fetch current UK grid carbon intensity and fuel mix. No auth required."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{UK_CARBON_API_BASE}/generation", timeout=10.0)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.warning("UK Carbon Intensity API fetch failed", error=str(e))
        return None

    try:
        gen_mix = data.get("data", {}).get("generationmix") or []
        if not gen_mix:
            return None

        # generationmix: [{"fuel": "gas", "perc": 35.5}, ...]
        fuel_factors = {
            "gas": 410,
            "coal": 820,
            "nuclear": 12,
            "wind": 11,
            "hydro": 4,
            "pumped": 24,
            "solar": 5,
            "biomass": 230,
            "other": 400,
            "imports": 300,  # approximate: varies by interconnector source
        }
        renewable_fuels = frozenset({"wind", "hydro", "pumped", "solar"})

        total_pct = sum(item.get("perc", 0) for item in gen_mix)
        if total_pct <= 0:
            return None

        carbon_sum = sum(
            item.get("perc", 0) * fuel_factors.get(item.get("fuel", "other"), 400)
            for item in gen_mix
        )
        renewable_pct = sum(
            item.get("perc", 0) for item in gen_mix
            if item.get("fuel") in renewable_fuels
        ) / total_pct

        # UK Carbon Intensity API also provides a direct intensity value in gCO2/kWh
        intensity_resp = await _fetch_uk_intensity_direct()
        if intensity_resp is not None:
            carbon_intensity = intensity_resp
        else:
            carbon_intensity = carbon_sum / total_pct

        return SustainabilityData(
            carbon_intensity_g_co2_kwh=carbon_intensity,
            renewable_energy_pct=min(1.0, renewable_pct),
            source="uk_api",
        )
    except Exception as e:
        logger.warning("UK Carbon Intensity API parse failed", error=str(e))
        return None


async def _fetch_uk_intensity_direct() -> float | None:
    """Fetch UK grid carbon intensity directly (gCO2/kWh) from the intensity endpoint."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{UK_CARBON_API_BASE}/intensity", timeout=8.0)
            r.raise_for_status()
            data = r.json()
        entries = data.get("data") or []
        if entries:
            intensity = entries[0].get("intensity", {}).get("actual")
            if intensity is not None:
                return float(intensity)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# EIA OpenData API (US grid — free, API key required)
# https://www.eia.gov/opendata/ — register for free API key
# ---------------------------------------------------------------------------

async def _fetch_from_eia(api_key: str, eia_respondent: str) -> SustainabilityData | None:
    """Fetch current fuel mix from EIA v2 API for a US grid region.

    Uses the electricity/rto/fuel-type-data endpoint (hourly generation by fuel type).
    """
    if not eia_respondent:
        return None
    now = datetime.now(timezone.utc)
    # Request last 24 hours
    start = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H")
    params = {
        "api_key": api_key,
        "frequency": "hourly",
        "data[0]": "value",
        "facets[respondent][]": eia_respondent,
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "length": 50,
        "start": start,
    }
    url = f"{EIA_API_BASE}/electricity/rto/fuel-type-data/data/"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.warning("EIA API fetch failed", respondent=eia_respondent, error=str(e))
        return None

    try:
        rows = (data.get("response") or {}).get("data") or []
        if not rows:
            return None

        # Get most recent period
        most_recent_period = rows[0].get("period")
        latest_rows = [r for r in rows if r.get("period") == most_recent_period]

        total_mwh = sum(float(r.get("value") or 0) for r in latest_rows)
        if total_mwh <= 0:
            return None

        carbon_sum = sum(
            float(r.get("value") or 0) * EIA_FUEL_EMISSION_FACTORS.get(r.get("fueltype", "OTH"), 400)
            for r in latest_rows
        )
        renewable_mwh = sum(
            float(r.get("value") or 0)
            for r in latest_rows
            if r.get("fueltype") in EIA_RENEWABLE_FUELS
        )

        return SustainabilityData(
            carbon_intensity_g_co2_kwh=carbon_sum / total_mwh,
            renewable_energy_pct=min(1.0, renewable_mwh / total_mwh),
            source="eia",
        )
    except Exception as e:
        logger.warning("EIA API parse failed", respondent=eia_respondent, error=str(e))
        return None


# ---------------------------------------------------------------------------
# Static country-level fallback (Ember Climate data, no network call)
# ---------------------------------------------------------------------------

def _get_country_static(country_code: str | None) -> SustainabilityData | None:
    """Return Ember Climate yearly average for a country. No network call."""
    if not country_code:
        return None
    entry = COUNTRY_STATIC_INTENSITY.get(country_code.upper())
    if not entry:
        return None
    return SustainabilityData(
        carbon_intensity_g_co2_kwh=entry[0],
        renewable_energy_pct=entry[1],
        source="static",
    )


# ---------------------------------------------------------------------------
# Country code inference from region slug
# ---------------------------------------------------------------------------

# Known region-slug → country-code mappings for common cloud providers
# (supplements DB lookup; used when DB is not available in the real-time path)
_SLUG_COUNTRY_HINTS: dict[str, str] = {
    # US slugs
    "us-east-1": "US", "us-east-2": "US", "us-west-1": "US", "us-west-2": "US",
    "eastus": "US", "eastus2": "US", "westus": "US", "westus2": "US", "westus3": "US",
    "centralus": "US", "northcentralus": "US", "southcentralus": "US",
    "nyc1": "US", "nyc2": "US", "nyc3": "US", "sfo2": "US", "sfo3": "US",
    # Canada
    "ca-central-1": "CA", "canadacentral": "CA", "canadaeast": "CA", "tor1": "CA",
    # UK
    "eu-west-2": "GB", "uksouth": "GB", "ukwest": "GB", "lon1": "GB",
    # Japan
    "ap-northeast-1": "JP", "japaneast": "JP", "japanwest": "JP",
    # Singapore
    "ap-southeast-1": "SG", "southeastasia": "SG", "sgp1": "SG",
    # Australia
    "ap-southeast-2": "AU", "australiaeast": "AU", "australiasoutheast": "AU", "syd1": "AU",
    # South Korea
    "ap-northeast-2": "KR", "koreacentral": "KR", "koreasouth": "KR",
    # India
    "ap-south-1": "IN", "centralindia": "IN", "southindia": "IN", "westindia": "IN",
    # Brazil
    "sa-east-1": "BR", "brazilsouth": "BR",
    # UAE
    "me-central-1": "AE", "uaenorth": "AE", "uaecentral": "AE",
    # South Africa
    "af-south-1": "ZA", "southafricanorth": "ZA",
    # Switzerland
    "eu-central-2": "CH", "switzerlandnorth": "CH", "switzerlandwest": "CH",
    # Israel
    "il-central-1": "IL", "israelcentral": "IL",
    # Mexico
    "mx-central-1": "MX",
    # New Zealand
    "ap-southeast-4": "NZ",
    # Hetzner non-EU
    "ash": "US", "hil": "US",
}


def _infer_country_from_slug(region_slug: str) -> str | None:
    """Infer ISO country code from a region slug."""
    return _SLUG_COUNTRY_HINTS.get(region_slug.lower())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_current_sustainability(
    provider_slug: str,
    region_slug: str,
    country_code: str | None = None,
) -> SustainabilityData | None:
    """Get current carbon intensity and renewable % for a region.

    Priority:
      1. Config override
      2. ENTSO-E (EU — free, token required)
      3. UK Carbon Intensity API (UK — free, no auth)
      4. EIA OpenData (US — free, API key required)
      5. Static country-level fallback (Ember Climate yearly averages)
      6. None → caller uses DB fallback

    Results are cached for settings.sustainability_cache_ttl_seconds.
    """
    provider_slug = provider_slug.strip().lower()
    region_slug = region_slug.strip().lower()

    cfg = await _get_config()

    # 1. Config override (bypasses cache)
    override = _get_region_override(cfg, provider_slug, region_slug)
    if override is not None:
        return override

    cache = _get_cache()
    cache_key = (provider_slug, region_slug)

    # Check cache
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    result: SustainabilityData | None = None

    # 2. ENTSO-E (EU)
    entsoe_area = _get_entsoe_area(cfg, provider_slug, region_slug)
    if entsoe_area and settings.entsoe_token:
        result = await _fetch_from_entsoe(settings.entsoe_token, entsoe_area)

    # 3. UK Carbon Intensity API (free, no auth)
    if result is None and region_slug in _UK_REGION_SLUGS:
        result = await _fetch_from_uk_carbon_api()

    # 4. EIA OpenData (US)
    if result is None and settings.eia_api_key:
        eia_respondent = EIA_REGION_MAP.get(region_slug, "")
        if eia_respondent:
            result = await _fetch_from_eia(settings.eia_api_key, eia_respondent)

    # 5. Static country-level fallback (no network call)
    if result is None:
        inferred_country = country_code or _infer_country_from_slug(region_slug)
        result = _get_country_static(inferred_country)

    if result is not None:
        await cache.set(cache_key, result)

    return result


async def reload_config() -> None:
    """Force reload of sustainability config (e.g. after YAML change)."""
    global _config
    async with _config_lock:
        _config = None
    logger.info("Sustainability config reloaded")
