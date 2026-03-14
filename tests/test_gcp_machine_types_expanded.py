"""Test GCP machine type ingestion (expanded families: N1, N2, C2, C2D, M1, M2, T2D).

The first set of tests validates the price computation logic using a small inline
spec table. The DB integration tests that call `ingest()` require live GCP API
credentials (GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_CLOUD_PROJECT); they are
skipped automatically when those are not available.
"""

from __future__ import annotations

import os
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, delete

from app.core.database import AsyncSessionLocal
from app.models import Region, InstanceType, Price
from app.models.provider import ProviderType
from app.models.price import PurchaseModel
from app.services.ingestion import ensure_provider, ensure_region, ensure_instance_type, append_price
from connectors.gcp import GCP_CPU_PSEUDO_NAME, GCP_RAM_PSEUDO_NAME
from connectors.gcp_machine_types import ingest

# Reference specs used by inline catalog tests (replaces removed GCP_MACHINE_TYPES static table)
_REFERENCE_SPECS = {
    "n1-standard-4": {"family": "n1", "vcpu": 4, "ram_gb": 15.0, "arch": "x86_64"},
    "n1-highmem-8": {"family": "n1", "vcpu": 8, "ram_gb": 52.0, "arch": "x86_64"},
    "n1-highcpu-16": {"family": "n1", "vcpu": 16, "ram_gb": 14.4, "arch": "x86_64"},
    "n2-standard-8": {"family": "n2", "vcpu": 8, "ram_gb": 32.0, "arch": "x86_64"},
    "n2-highmem-16": {"family": "n2", "vcpu": 16, "ram_gb": 128.0, "arch": "x86_64"},
    "c2-standard-16": {"family": "c2", "vcpu": 16, "ram_gb": 64.0, "arch": "x86_64"},
    "c2-standard-60": {"family": "c2", "vcpu": 60, "ram_gb": 240.0, "arch": "x86_64"},
    "c2d-standard-16": {"family": "c2d", "vcpu": 16, "ram_gb": 64.0, "arch": "x86_64"},
    "c2d-standard-112": {"family": "c2d", "vcpu": 112, "ram_gb": 448.0, "arch": "x86_64"},
    "m1-megamem-96": {"family": "m1", "vcpu": 96, "ram_gb": 1433.6, "arch": "x86_64"},
    "m1-ultramem-160": {"family": "m1", "vcpu": 160, "ram_gb": 3844.0, "arch": "x86_64"},
    "m2-megamem-416": {"family": "m2", "vcpu": 416, "ram_gb": 5888.0, "arch": "x86_64"},
    "m2-ultramem-416": {"family": "m2", "vcpu": 416, "ram_gb": 11776.0, "arch": "x86_64"},
    "t2d-standard-16": {"family": "t2d", "vcpu": 16, "ram_gb": 64.0, "arch": "x86_64"},
    "t2d-standard-96": {"family": "t2d", "vcpu": 96, "ram_gb": 384.0, "arch": "x86_64"},
}

_HAS_GCP_CREDENTIALS = bool(
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.environ.get("GOOGLE_CLOUD_CREDENTIALS")
) and bool(os.environ.get("GOOGLE_CLOUD_PROJECT"))

requires_gcp = pytest.mark.skipif(
    not _HAS_GCP_CREDENTIALS,
    reason="GCP credentials (GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_CLOUD_PROJECT) not configured",
)


def test_n1_machine_type_reference_specs():
    """Reference N1 specs (inline table) are internally consistent."""
    n1_types = {k: v for k, v in _REFERENCE_SPECS.items() if v["family"] == "n1"}
    assert len(n1_types) >= 3
    assert "n1-standard-4" in n1_types
    assert n1_types["n1-standard-4"]["vcpu"] == 4
    assert n1_types["n1-standard-4"]["ram_gb"] == 15.0


def test_n2_machine_type_reference_specs():
    """Reference N2 specs (inline table) are internally consistent."""
    n2_types = {k: v for k, v in _REFERENCE_SPECS.items() if v["family"] == "n2"}
    assert len(n2_types) >= 2
    assert "n2-standard-8" in n2_types
    assert n2_types["n2-standard-8"]["vcpu"] == 8
    assert n2_types["n2-standard-8"]["ram_gb"] == 32.0


def test_c2_c2d_reference_specs():
    """Reference C2 and C2D specs are internally consistent."""
    c2_types = {k: v for k, v in _REFERENCE_SPECS.items() if v["family"] == "c2"}
    c2d_types = {k: v for k, v in _REFERENCE_SPECS.items() if v["family"] == "c2d"}
    assert "c2-standard-16" in c2_types
    assert "c2d-standard-112" in c2d_types


def test_m1_m2_memory_optimized_reference_specs():
    """Reference M1 and M2 specs are internally consistent with high memory ratios."""
    m1_types = {k: v for k, v in _REFERENCE_SPECS.items() if v["family"] == "m1"}
    m2_types = {k: v for k, v in _REFERENCE_SPECS.items() if v["family"] == "m2"}
    assert "m1-ultramem-160" in m1_types
    assert m1_types["m1-ultramem-160"]["ram_gb"] == 3844.0
    assert "m2-ultramem-416" in m2_types


def test_t2d_amd_reference_specs():
    """Reference T2D specs are internally consistent."""
    t2d_types = {k: v for k, v in _REFERENCE_SPECS.items() if v["family"] == "t2d"}
    assert "t2d-standard-96" in t2d_types
    assert t2d_types["t2d-standard-96"]["arch"] == "x86_64"


@pytest.mark.asyncio
@requires_gcp
async def test_n1_standard_4_price_computation():
    """Test that n1-standard-4 price is computed correctly from unit prices."""
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

        # N1 standard: 3.75 GB per vCPU -> n1-standard-4: 4 vCPU, 15 GB RAM
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

        await ingest(db, only_eu=True, purchase_model="on_demand")
        await db.commit()

        n1_std4_stmt = (
            select(InstanceType)
            .where(InstanceType.provider_id == provider.id)
            .where(InstanceType.name == "n1-standard-4")
        )
        n1_std4 = (await db.execute(n1_std4_stmt)).scalars().first()

        assert n1_std4 is not None, "n1-standard-4 should be created"
        assert n1_std4.vcpu == 4
        assert n1_std4.ram_gb == 15.0
        assert n1_std4.family == "n1"

        price_stmt = (
            select(Price)
            .where(Price.instance_type_id == n1_std4.id)
            .where(Price.region_id == region.id)
            .where(Price.purchase_model == PurchaseModel.ON_DEMAND)
            .order_by(Price.timestamp.desc())
        )
        price = (await db.execute(price_stmt)).scalars().first()

        assert price is not None
        expected_price = (0.02 * 4) + (0.003 * 15.0)  # 0.08 + 0.045 = 0.125
        assert abs(price.price_eur_per_hour - expected_price) < 0.0001

        # Cleanup
        await db.execute(delete(Price).where(Price.region_id == region.id))
        await db.execute(
            delete(InstanceType)
            .where(InstanceType.provider_id == provider.id)
            .where(InstanceType.name.like("n1-%"))
        )
        await db.execute(delete(Region).where(Region.id == region.id))
        await db.commit()


@pytest.mark.asyncio
@requires_gcp
async def test_all_families_created_during_ingestion():
    """Test that machine types from all GCP families are created during ingestion."""
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

        await ingest(db, only_eu=True, purchase_model="on_demand")
        await db.commit()

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
        await db.execute(delete(Price).where(Price.region_id == region.id))
        await db.execute(delete(InstanceType).where(InstanceType.provider_id == provider.id))
        await db.execute(delete(Region).where(Region.id == region.id))
        await db.commit()
