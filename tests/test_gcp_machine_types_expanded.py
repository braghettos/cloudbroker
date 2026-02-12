"""Test expanded GCP machine type catalog (N1, N2, C2, C2D, M1, M2, T2D families)."""

import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, delete

from app.core.database import AsyncSessionLocal
from app.models import Region, InstanceType, Price
from app.models.provider import ProviderType
from app.models.price import PurchaseModel
from app.services.ingestion import ensure_provider, ensure_region, ensure_instance_type, append_price
from connectors.gcp import GCP_CPU_PSEUDO_NAME, GCP_RAM_PSEUDO_NAME
from connectors.gcp_machine_types import GCP_MACHINE_TYPES, ingest


@pytest.mark.asyncio
async def test_n1_machine_types_in_catalog():
    """Test that N1 machine types are in the catalog."""
    n1_types = [m for m in GCP_MACHINE_TYPES if m.family == "n1"]
    assert len(n1_types) > 0, "N1 machine types should be in catalog"

    # Check some expected N1 types
    n1_names = {m.name for m in n1_types}
    assert "n1-standard-4" in n1_names
    assert "n1-highmem-8" in n1_names
    assert "n1-highcpu-16" in n1_names

    # Verify specs
    n1_std4 = next(m for m in n1_types if m.name == "n1-standard-4")
    assert n1_std4.vcpu == 4
    assert n1_std4.ram_gb == 4 * 3.75  # N1 standard: 3.75 GB per vCPU


@pytest.mark.asyncio
async def test_n2_machine_types_in_catalog():
    """Test that N2 machine types are in the catalog."""
    n2_types = [m for m in GCP_MACHINE_TYPES if m.family == "n2"]
    assert len(n2_types) > 0, "N2 machine types should be in catalog"

    n2_names = {m.name for m in n2_types}
    assert "n2-standard-8" in n2_names
    assert "n2-highmem-16" in n2_names

    n2_std8 = next(m for m in n2_types if m.name == "n2-standard-8")
    assert n2_std8.vcpu == 8
    assert n2_std8.ram_gb == 8 * 4.0  # N2 standard: 4 GB per vCPU


@pytest.mark.asyncio
async def test_c2_c2d_machine_types_in_catalog():
    """Test that C2 and C2D compute-optimized types are in catalog."""
    c2_types = [m for m in GCP_MACHINE_TYPES if m.family == "c2"]
    c2d_types = [m for m in GCP_MACHINE_TYPES if m.family == "c2d"]

    assert len(c2_types) > 0, "C2 machine types should be in catalog"
    assert len(c2d_types) > 0, "C2D machine types should be in catalog"

    c2_names = {m.name for m in c2_types}
    assert "c2-standard-16" in c2_names
    assert "c2-standard-60" in c2_names

    c2d_names = {m.name for m in c2d_types}
    assert "c2d-standard-16" in c2d_names
    assert "c2d-standard-112" in c2d_names


@pytest.mark.asyncio
async def test_m1_m2_memory_optimized_in_catalog():
    """Test that M1 and M2 memory-optimized types are in catalog."""
    m1_types = [m for m in GCP_MACHINE_TYPES if m.family == "m1"]
    m2_types = [m for m in GCP_MACHINE_TYPES if m.family == "m2"]

    assert len(m1_types) > 0, "M1 machine types should be in catalog"
    assert len(m2_types) > 0, "M2 machine types should be in catalog"

    m1_names = {m.name for m in m1_types}
    assert "m1-megamem-96" in m1_names
    assert "m1-ultramem-160" in m1_names

    m2_names = {m.name for m in m2_types}
    assert "m2-megamem-416" in m2_names
    assert "m2-ultramem-416" in m2_names

    # Verify high memory ratios
    m1_ultramem_160 = next(m for m in m1_types if m.name == "m1-ultramem-160")
    assert m1_ultramem_160.vcpu == 160
    assert m1_ultramem_160.ram_gb == 3844.0  # Very high RAM


@pytest.mark.asyncio
async def test_t2d_amd_types_in_catalog():
    """Test that T2D AMD-based types are in catalog."""
    t2d_types = [m for m in GCP_MACHINE_TYPES if m.family == "t2d"]
    assert len(t2d_types) > 0, "T2D machine types should be in catalog"

    t2d_names = {m.name for m in t2d_types}
    assert "t2d-standard-16" in t2d_names
    assert "t2d-standard-96" in t2d_names

    # Verify AMD architecture
    t2d_std16 = next(m for m in t2d_types if m.name == "t2d-standard-16")
    assert t2d_std16.arch == "x86_64"


@pytest.mark.asyncio
async def test_n1_standard_4_price_computation():
    """Test that n1-standard-4 price is computed correctly."""
    base_time = datetime(2020, 1, 3, 0, 0, 0, tzinfo=timezone.utc)
    cpu_time = base_time
    ram_time = base_time + timedelta(hours=1)

    async with AsyncSessionLocal() as db:
        provider = await ensure_provider(
            db,
            slug="gcp",
            name="Google Cloud",
            website="https://cloud.google.com",
            type=ProviderType.HYPERSCALER,
        )
        await db.flush()

        region = await ensure_region(
            db,
            provider_id=provider.id,
            slug="europe-west1-test-n1",
            name="europe-west1-test-n1",
            is_eu=True,
        )
        await db.flush()

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

        # N1 standard: 3.75 GB per vCPU
        # n1-standard-4: 4 vCPU, 15 GB RAM
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
        assert cpu_price is not None
        assert ram_price is not None
        await db.commit()

        # Run ingestion
        await ingest(db, only_eu=True, purchase_model="on_demand")
        await db.commit()

        # Verify n1-standard-4 was created
        n1_std4_stmt = (
            select(InstanceType)
            .where(InstanceType.provider_id == provider.id)
            .where(InstanceType.name == "n1-standard-4")
        )
        n1_std4_result = await db.execute(n1_std4_stmt)
        n1_std4 = n1_std4_result.scalars().first()

        assert n1_std4 is not None, "n1-standard-4 should be created"
        assert n1_std4.vcpu == 4
        assert n1_std4.ram_gb == 15.0  # 4 * 3.75
        assert n1_std4.family == "n1"

        # Verify price
        price_stmt = (
            select(Price)
            .where(Price.instance_type_id == n1_std4.id)
            .where(Price.region_id == region.id)
            .where(Price.purchase_model == PurchaseModel.ON_DEMAND)
            .order_by(Price.timestamp.desc())
        )
        price_result = await db.execute(price_stmt)
        price = price_result.scalars().first()

        assert price is not None
        expected_price = (0.02 * 4) + (0.003 * 15.0)  # 0.08 + 0.045 = 0.125
        assert abs(price.price_eur_per_hour - expected_price) < 0.0001

        # Cleanup
        try:
            await db.execute(delete(Price).where(Price.region_id == region.id))
            await db.execute(delete(InstanceType).where(
                InstanceType.provider_id == provider.id
            ).where(InstanceType.name.like("n1-%")))
            await db.execute(delete(Region).where(Region.id == region.id))
            await db.commit()
        finally:
            await db.close()


@pytest.mark.asyncio
async def test_all_families_created_during_ingestion():
    """Test that machine types from all families are created during ingestion."""
    base_time = datetime(2020, 1, 4, 0, 0, 0, tzinfo=timezone.utc)
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
            slug="europe-west1-test-all-families",
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

        await append_price(
            db,
            instance_type_id=cpu_it.id,
            region_id=region.id,
            purchase_model=PurchaseModel.ON_DEMAND,
            currency="EUR",
            price_per_hour=0.02,
            price_eur_per_hour=0.02,
            timestamp=cpu_time,
        )
        await append_price(
            db,
            instance_type_id=ram_it.id,
            region_id=region.id,
            purchase_model=PurchaseModel.ON_DEMAND,
            currency="EUR",
            price_per_hour=0.003,
            price_eur_per_hour=0.003,
            timestamp=ram_time,
        )
        await db.commit()

        # Run ingestion
        await ingest(db, only_eu=True, purchase_model="on_demand")
        await db.commit()

        # Verify at least one type from each family exists
        families = ["e2", "n1", "n2", "c2", "c2d", "m1", "m2", "t2d"]
        for family in families:
            stmt = (
                select(InstanceType)
                .where(InstanceType.provider_id == provider.id)
                .where(InstanceType.family == family)
                .limit(1)
            )
            result = await db.execute(stmt)
            instance_type = result.scalars().first()
            assert instance_type is not None, f"At least one {family} machine type should be created"

        # Cleanup
        try:
            await db.execute(delete(Price).where(Price.region_id == region.id))
            await db.execute(delete(InstanceType).where(
                InstanceType.provider_id == provider.id
            ))
            await db.execute(delete(Region).where(Region.id == region.id))
            await db.commit()
        finally:
            await db.close()
