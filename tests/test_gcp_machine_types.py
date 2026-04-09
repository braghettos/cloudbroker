"""Test GCP machine type price computation from unit prices.

Tests that call `ingest()` require live GCP credentials (GOOGLE_APPLICATION_CREDENTIALS
+ GOOGLE_CLOUD_PROJECT) and are skipped automatically when those are absent.
"""

import os
import pytest
from datetime import datetime, timezone, timedelta

from app.core.database import AsyncSessionLocal
from app.models import Provider, Region, InstanceType, Price
from app.models.provider import ProviderType
from app.models.price import PurchaseModel
from app.services.ingestion import ensure_provider, ensure_region, ensure_instance_type, append_price
from connectors.gcp import GCP_CPU_PSEUDO_NAME, GCP_RAM_PSEUDO_NAME
from connectors.gcp_machine_types import ingest

# Minimal reference specs for the second test (previously from static catalog)
_E2_SPOT_CHECK = [
    ("e2-standard-2", 2, 8.0, "e2", "x86_64"),
    ("e2-standard-4", 4, 16.0, "e2", "x86_64"),
    ("e2-highmem-4", 4, 32.0, "e2", "x86_64"),
    ("e2-highcpu-4", 4, 4.0, "e2", "x86_64"),
    ("e2-medium", 2, 4.0, "e2", "x86_64"),
]

_HAS_GCP_CREDENTIALS = bool(
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.environ.get("GOOGLE_CLOUD_CREDENTIALS")
) and bool(os.environ.get("GOOGLE_CLOUD_PROJECT"))

requires_gcp = pytest.mark.skipif(
    not _HAS_GCP_CREDENTIALS,
    reason="GCP credentials (GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_CLOUD_PROJECT) not configured",
)


@pytest.mark.asyncio
@requires_gcp
async def test_e2_standard_2_price_computation():
    """
    Test that e2-standard-2 price is computed correctly:
    Given: cpu_price = 0.02 EUR/vcpu-hour, ram_price = 0.003 EUR/gb-hour
    Expected: e2-standard-2 (2 vCPU, 8 GB RAM) = 2*0.02 + 8*0.003 = 0.064 EUR/hour
    """
    # Use unique timestamps far in the past to avoid deduplication with existing data
    # Use a fixed timestamp to ensure consistency
    base_time = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    cpu_time = base_time
    ram_time = base_time + timedelta(hours=1)

    async with AsyncSessionLocal() as db:
        # Setup: Create GCP provider (ingest function requires slug "gcp")
        provider = await ensure_provider(
            db,
            slug="gcp",
            name="Google Cloud",
            website="https://cloud.google.com",
            type=ProviderType.HYPERSCALER,
        )
        await db.flush()

        # Create a test region with unique slug to avoid conflicts
        region = await ensure_region(
            db,
            provider_id=provider.id,
            slug="europe-west1-test-unique",
            name="europe-west1-test",
            is_eu=True,
        )
        await db.flush()

        # Create pseudo instance types for unit prices
        cpu_it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=GCP_CPU_PSEUDO_NAME,
            vcpu=1,
            ram_gb=0.01,
            family="compute",
        )
        ram_it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=GCP_RAM_PSEUDO_NAME,
            vcpu=1,
            ram_gb=1.0,
            family="compute",
        )
        await db.flush()

        # Insert unit prices: CPU = 0.02 EUR/vcpu-hour, RAM = 0.003 EUR/gb-hour
        # Use different hours to avoid deduplication
        cpu_price = await append_price(
            db,
            instance_type_id=cpu_it.id,
            region_id=region.id,
            purchase_model=PurchaseModel.ON_DEMAND,
            currency="EUR",
            price_per_hour=0.02,
            price_eur_per_hour=0.02,
            timestamp=cpu_time,
        )
        ram_price = await append_price(
            db,
            instance_type_id=ram_it.id,
            region_id=region.id,
            purchase_model=PurchaseModel.ON_DEMAND,
            currency="EUR",
            price_per_hour=0.003,
            price_eur_per_hour=0.003,
            timestamp=ram_time,
        )
        assert cpu_price is not None, "CPU price should be inserted"
        assert ram_price is not None, "RAM price should be inserted"
        await db.commit()

        # Run machine type ingestion
        await ingest(db, only_eu=True, purchase_model="on_demand")
        await db.commit()

        # Verify e2-standard-2 was created
        from sqlalchemy import select, delete

        e2_std2_stmt = (
            select(InstanceType)
            .where(InstanceType.provider_id == provider.id)
            .where(InstanceType.name == "e2-standard-2")
        )
        e2_std2_result = await db.execute(e2_std2_stmt)
        e2_std2 = e2_std2_result.scalars().first()

        assert e2_std2 is not None, "e2-standard-2 instance type should be created"
        assert e2_std2.vcpu == 2
        assert e2_std2.ram_gb == 8.0
        assert e2_std2.family == "e2"
        assert e2_std2.arch == "x86_64"

        # Verify price was computed correctly
        price_stmt = (
            select(Price)
            .where(Price.instance_type_id == e2_std2.id)
            .where(Price.region_id == region.id)
            .where(Price.purchase_model == PurchaseModel.ON_DEMAND)
            .order_by(Price.timestamp.desc())
        )
        price_result = await db.execute(price_stmt)
        price = price_result.scalars().first()

        assert price is not None, "Price should be computed for e2-standard-2"
        # Expected: 2 * 0.02 + 8 * 0.003 = 0.04 + 0.024 = 0.064
        expected_price = 0.064
        assert abs(price.price_eur_per_hour - expected_price) < 0.0001, (
            f"Expected {expected_price}, got {price.price_eur_per_hour}"
        )

        # Cleanup: delete in order respecting foreign keys
        await db.execute(
            delete(Price).where(
                Price.instance_type_id.in_(select(InstanceType.id).where(InstanceType.provider_id == provider.id))
            )
        )
        await db.execute(delete(InstanceType).where(InstanceType.provider_id == provider.id))
        await db.execute(delete(Region).where(Region.provider_id == provider.id))
        await db.execute(delete(Provider).where(Provider.id == provider.id))
        await db.commit()


@pytest.mark.asyncio
@requires_gcp
async def test_all_e2_machine_types_created():
    """Test that all E2 machine types from catalog are created."""
    # Note: This test still focuses on E2 for backward compatibility
    # Use unique timestamps far in the past
    base_time = datetime(2020, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
    cpu_time = base_time
    ram_time = base_time + timedelta(hours=1)

    async with AsyncSessionLocal() as db:
        provider = await ensure_provider(
            db,
            slug="gcp",
            name="Google Cloud",
            type=ProviderType.HYPERSCALER,
        )
        await db.flush()

        region = await ensure_region(
            db,
            provider_id=provider.id,
            slug="europe-west1-test-all",
            name="europe-west1-test-all",
            is_eu=True,
        )
        await db.flush()

        cpu_it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=GCP_CPU_PSEUDO_NAME,
            vcpu=1,
            ram_gb=0.01,
        )
        ram_it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=GCP_RAM_PSEUDO_NAME,
            vcpu=1,
            ram_gb=1.0,
        )
        await db.flush()

        cpu_price = await append_price(
            db,
            instance_type_id=cpu_it.id,
            region_id=region.id,
            purchase_model=PurchaseModel.ON_DEMAND,
            currency="EUR",
            price_per_hour=0.02,
            price_eur_per_hour=0.02,
            timestamp=cpu_time,
        )
        ram_price = await append_price(
            db,
            instance_type_id=ram_it.id,
            region_id=region.id,
            purchase_model=PurchaseModel.ON_DEMAND,
            currency="EUR",
            price_per_hour=0.003,
            price_eur_per_hour=0.003,
            timestamp=ram_time,
        )
        assert cpu_price is not None, "CPU price should be inserted"
        assert ram_price is not None, "RAM price should be inserted"
        await db.commit()

        # Run ingestion
        await ingest(db, only_eu=True, purchase_model="on_demand")
        await db.commit()

        # Verify common E2 machine types exist (spot-check; live catalog may have more)
        from sqlalchemy import select, delete

        for name, vcpu, ram_gb, family, arch in _E2_SPOT_CHECK:
            stmt = select(InstanceType).where(InstanceType.provider_id == provider.id).where(InstanceType.name == name)
            result = await db.execute(stmt)
            instance_type = result.scalars().first()
            assert instance_type is not None, f"Machine type {name} not found"
            assert instance_type.vcpu == vcpu
            assert instance_type.ram_gb == ram_gb
            assert instance_type.family == family
            assert instance_type.arch == arch

        # Cleanup: delete in order respecting foreign keys
        await db.execute(
            delete(Price).where(
                Price.instance_type_id.in_(select(InstanceType.id).where(InstanceType.provider_id == provider.id))
            )
        )
        await db.execute(delete(InstanceType).where(InstanceType.provider_id == provider.id))
        await db.execute(delete(Region).where(Region.provider_id == provider.id))
        await db.execute(delete(Provider).where(Provider.id == provider.id))
        await db.commit()
