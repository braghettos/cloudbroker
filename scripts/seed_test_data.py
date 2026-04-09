"""Seed realistic test data for CI and local testing.

Creates a representative dataset across multiple provider types, regions,
instance types, prices, FX rates, and cost components. Designed to exercise
all code paths in the recommendation engine, scoring, TCO, and analytics.

Usage:
    python scripts/seed_test_data.py

Requires DATABASE_URL environment variable (or .env file).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.models import (
    EgressRate,
    InstanceType,
    OsLicenseRate,
    Price,
    Provider,
    PublicIpRate,
    Region,
    StorageRate,
)
from app.models.price import PurchaseModel
from app.models.provider import ProviderType
from app.models.fx_rate import FxRate


async def seed() -> None:
    """Seed the database with realistic test data."""
    async with AsyncSessionLocal() as db:
        # Check if already seeded
        result = await db.execute(text("SELECT count(*) FROM providers WHERE slug LIKE 'seed-%'"))
        count = result.scalar_one()
        if count > 0:
            print(f"Already seeded ({count} seed providers). Skipping.")
            return

        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(hours=24)
        two_days_ago = now - timedelta(hours=48)

        # ── FX Rates ─────────────────────────────────────────────────────
        fx_usd = FxRate(from_currency="USD", to_currency="EUR", rate=0.92, effective_date=now)
        fx_eur = FxRate(from_currency="EUR", to_currency="EUR", rate=1.0, effective_date=now)
        fx_chf = FxRate(from_currency="CHF", to_currency="EUR", rate=0.95, effective_date=now)
        db.add_all([fx_usd, fx_eur, fx_chf])
        await db.flush()

        # ── Providers ────────────────────────────────────────────────────
        p_hyper = Provider(slug="seed-aws", name="Seed AWS", website="https://aws.example.com", type=ProviderType.HYPERSCALER)
        p_eu = Provider(slug="seed-hetzner", name="Seed Hetzner", website="https://hetzner.example.com", type=ProviderType.EU)
        p_regional = Provider(slug="seed-contabo", name="Seed Contabo", website="https://contabo.example.com", type=ProviderType.REGIONAL)
        db.add_all([p_hyper, p_eu, p_regional])
        await db.flush()

        # ── Regions ──────────────────────────────────────────────────────
        r_eu1 = Region(provider_id=p_hyper.id, slug="seed-eu-west-1", name="EU West 1", country="IE", is_eu=True, renewable_energy_pct=0.40, carbon_intensity_g_co2_kwh=300.0)
        r_us1 = Region(provider_id=p_hyper.id, slug="seed-us-east-1", name="US East 1", country="US", is_eu=False, renewable_energy_pct=0.20, carbon_intensity_g_co2_kwh=450.0)
        r_de1 = Region(provider_id=p_eu.id, slug="seed-fsn1", name="Falkenstein", country="DE", is_eu=True, renewable_energy_pct=0.55, carbon_intensity_g_co2_kwh=350.0)
        r_fi1 = Region(provider_id=p_eu.id, slug="seed-hel1", name="Helsinki", country="FI", is_eu=True, renewable_energy_pct=0.80, carbon_intensity_g_co2_kwh=100.0)
        r_de2 = Region(provider_id=p_regional.id, slug="seed-de-1", name="Germany 1", country="DE", is_eu=True)
        db.add_all([r_eu1, r_us1, r_de1, r_fi1, r_de2])
        await db.flush()

        # ── Instance Types ───────────────────────────────────────────────
        # Hyperscaler: varied families, arch, CPU types
        it_t3m = InstanceType(provider_id=p_hyper.id, name="seed-t3.medium", family="general-purpose", vcpu=2, ram_gb=4.0, arch="x86_64", cpu_type="shared", is_current_gen=True, network_performance="Up to 5 Gbps")
        it_t3l = InstanceType(provider_id=p_hyper.id, name="seed-t3.large", family="general-purpose", vcpu=2, ram_gb=8.0, arch="x86_64", cpu_type="shared", is_current_gen=True)
        it_m5l = InstanceType(provider_id=p_hyper.id, name="seed-m5.large", family="general-purpose", vcpu=2, ram_gb=8.0, arch="x86_64", cpu_type="dedicated", is_current_gen=True)
        it_m5x = InstanceType(provider_id=p_hyper.id, name="seed-m5.xlarge", family="general-purpose", vcpu=4, ram_gb=16.0, arch="x86_64", cpu_type="dedicated", is_current_gen=True)
        it_c5l = InstanceType(provider_id=p_hyper.id, name="seed-c5.large", family="compute-optimized", vcpu=2, ram_gb=4.0, arch="x86_64", cpu_type="dedicated", is_current_gen=True)
        it_r5l = InstanceType(provider_id=p_hyper.id, name="seed-r5.large", family="memory-optimized", vcpu=2, ram_gb=16.0, arch="x86_64", cpu_type="dedicated", is_current_gen=True)
        it_g4 = InstanceType(provider_id=p_hyper.id, name="seed-g4dn.xlarge", family="gpu", vcpu=4, ram_gb=16.0, arch="x86_64", cpu_type="dedicated", gpu_count=1, gpu_model="nvidia-t4", gpu_memory_gb=16.0, is_current_gen=True)
        it_t4g = InstanceType(provider_id=p_hyper.id, name="seed-t4g.medium", family="general-purpose", vcpu=2, ram_gb=4.0, arch="arm64", cpu_type="shared", is_current_gen=True)
        it_m6g = InstanceType(provider_id=p_hyper.id, name="seed-m6g.large", family="general-purpose", vcpu=2, ram_gb=8.0, arch="arm64", cpu_type="dedicated", is_current_gen=True)
        # Old gen (is_current_gen=False)
        it_t2m = InstanceType(provider_id=p_hyper.id, name="seed-t2.medium", family="general-purpose", vcpu=2, ram_gb=4.0, arch="x86_64", cpu_type="shared", is_current_gen=False)

        # EU provider: Hetzner-like
        it_cx22 = InstanceType(provider_id=p_eu.id, name="seed-cx22", family="general-purpose", vcpu=2, ram_gb=4.0, arch="x86_64", cpu_type="shared", disk_gb=40.0, disk_type="local_ssd", is_current_gen=True)
        it_cx32 = InstanceType(provider_id=p_eu.id, name="seed-cx32", family="general-purpose", vcpu=4, ram_gb=8.0, arch="x86_64", cpu_type="shared", disk_gb=80.0, disk_type="local_ssd", is_current_gen=True)
        it_cpx22 = InstanceType(provider_id=p_eu.id, name="seed-cpx22", family="compute-optimized", vcpu=3, ram_gb=4.0, arch="x86_64", cpu_type="dedicated", disk_gb=80.0, disk_type="local_ssd", is_current_gen=True)
        it_cax11 = InstanceType(provider_id=p_eu.id, name="seed-cax11", family="general-purpose", vcpu=2, ram_gb=4.0, arch="arm64", cpu_type="shared", disk_gb=40.0, disk_type="local_ssd", is_current_gen=True, transfer_tb=20.0)
        # Unavailable instance
        it_cx11 = InstanceType(provider_id=p_eu.id, name="seed-cx11-unavail", family="general-purpose", vcpu=1, ram_gb=2.0, arch="x86_64", is_available=False)

        # Regional provider: Contabo-like
        it_vps1 = InstanceType(provider_id=p_regional.id, name="seed-vps-s", family="general-purpose", vcpu=4, ram_gb=8.0, arch="x86_64", cpu_type="shared", disk_gb=75.0, disk_type="local_ssd", is_current_gen=True)
        it_vps2 = InstanceType(provider_id=p_regional.id, name="seed-vps-m", family="general-purpose", vcpu=6, ram_gb=16.0, arch="x86_64", cpu_type="shared", disk_gb=150.0, disk_type="local_ssd", is_current_gen=True)
        it_vds1 = InstanceType(provider_id=p_regional.id, name="seed-vds-s", family="compute-optimized", vcpu=4, ram_gb=16.0, arch="x86_64", cpu_type="dedicated", disk_gb=200.0, disk_type="local_ssd", is_current_gen=True)

        all_its = [it_t3m, it_t3l, it_m5l, it_m5x, it_c5l, it_r5l, it_g4, it_t4g, it_m6g, it_t2m,
                   it_cx22, it_cx32, it_cpx22, it_cax11, it_cx11,
                   it_vps1, it_vps2, it_vds1]
        db.add_all(all_its)
        await db.flush()

        # ── Prices ───────────────────────────────────────────────────────
        prices = []

        def add_price(it, region, eur, model=PurchaseModel.ON_DEMAND, ts=None, currency="EUR"):
            prices.append(Price(
                instance_type_id=it.id, region_id=region.id,
                purchase_model=model, currency=currency,
                price_per_hour=eur, price_eur_per_hour=eur,
                timestamp=ts or now,
            ))

        # Hyperscaler: EU and US regions, on_demand + spot
        for it, price_eur in [(it_t3m, 0.0464), (it_t3l, 0.0928), (it_m5l, 0.107),
                               (it_m5x, 0.214), (it_c5l, 0.096), (it_r5l, 0.148),
                               (it_g4, 0.71), (it_t4g, 0.0372), (it_m6g, 0.086), (it_t2m, 0.052)]:
            add_price(it, r_eu1, price_eur)
            add_price(it, r_us1, price_eur * 0.95)  # US slightly cheaper
            # Spot prices (30-70% of on-demand)
            if it != it_g4:  # GPU no spot
                add_price(it, r_eu1, price_eur * 0.4, PurchaseModel.SPOT)

        # Historical prices for analytics (48h ago vs 24h ago — 5% increase)
        add_price(it_t3m, r_eu1, 0.0440, ts=two_days_ago)
        add_price(it_t3m, r_eu1, 0.0464, ts=yesterday)

        # EU provider: EUR native
        for it, price_eur in [(it_cx22, 0.0072), (it_cx32, 0.0136), (it_cpx22, 0.0102), (it_cax11, 0.0064)]:
            add_price(it, r_de1, price_eur)
            add_price(it, r_fi1, price_eur)

        # Regional provider: EUR native
        for it, price_eur in [(it_vps1, 0.0049), (it_vps2, 0.0077), (it_vds1, 0.038)]:
            add_price(it, r_de2, price_eur)

        db.add_all(prices)
        await db.flush()

        # ── Cost Components ──────────────────────────────────────────────
        # Egress rates
        eg_hyper = EgressRate(provider_slug="seed-aws", egress_eur_per_gb=0.085, free_tier_gb_per_month=100.0, cross_region_eur_per_gb=0.02, cross_az_eur_per_gb=0.01)
        eg_eu = EgressRate(provider_slug="seed-hetzner", egress_eur_per_gb=0.001, free_tier_gb_per_month=20000.0)
        eg_regional = EgressRate(provider_slug="seed-contabo", egress_eur_per_gb=0.0, free_tier_gb_per_month=0.0)
        db.add_all([eg_hyper, eg_eu, eg_regional])

        # Storage rates
        for slug, rates in [
            ("seed-aws", [("ssd", 0.08), ("hdd", 0.045), ("balanced", 0.065)]),
            ("seed-hetzner", [("ssd", 0.052)]),
        ]:
            for stype, eur in rates:
                db.add(StorageRate(provider_slug=slug, storage_type=stype, eur_per_gb_month=eur))

        # Public IP rates
        db.add(PublicIpRate(provider_slug="seed-aws", eur_per_hour=0.005))
        db.add(PublicIpRate(provider_slug="seed-hetzner", eur_per_hour=0.0008))

        # OS license rates
        for slug in ["seed-aws", "seed-hetzner"]:
            db.add(OsLicenseRate(provider_slug=slug, os_type="windows", eur_per_vcpu_hour=0.02))
            db.add(OsLicenseRate(provider_slug=slug, os_type="rhel", eur_per_vcpu_hour=0.015))

        await db.commit()

        # Summary
        print("Seeded test data:")
        print(f"  Providers: 3 (hyperscaler, EU, regional)")
        print(f"  Regions: 5 (3 EU, 2 non-EU)")
        print(f"  Instance types: {len(all_its)} (x86+ARM, shared+dedicated, GPU, old-gen, unavailable)")
        print(f"  Prices: {len(prices)} (on_demand + spot + historical)")
        print(f"  FX rates: 3 (USD, EUR, CHF)")
        print(f"  Egress rates: 3")
        print(f"  Storage rates: 4")
        print(f"  Public IP rates: 2")
        print(f"  OS license rates: 4")


if __name__ == "__main__":
    asyncio.run(seed())
