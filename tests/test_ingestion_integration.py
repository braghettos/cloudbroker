"""Integration tests for ingestion connectors.

Calls real ingest() functions within pytest so coverage captures the code paths.
Config-based providers need no auth. Public API providers call real endpoints.
Auth-required providers are skipped if credentials are not set.

These tests modify the database (insert providers, regions, instance types, prices).
They are idempotent — safe to re-run.
"""

import pytest
from sqlalchemy import func, select, text

from app.core.database import AsyncSessionLocal
from app.models import EgressRate, InstanceType, OsLicenseRate, Price, Provider, PublicIpRate, Region, StorageRate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _count(model):
    """Count rows in a model table."""
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(func.count()).select_from(model))).scalar_one()


async def _provider_stats(slug: str) -> dict:
    """Get instance_type and price counts for a provider."""
    async with AsyncSessionLocal() as db:
        provider = (
            await db.execute(select(Provider).where(Provider.slug == slug).limit(1))
        ).scalars().first()
        if not provider:
            return {"exists": False}
        it_count = (
            await db.execute(
                select(func.count()).select_from(InstanceType).where(InstanceType.provider_id == provider.id)
            )
        ).scalar_one()
        price_count = (
            await db.execute(
                select(func.count())
                .select_from(Price)
                .join(InstanceType)
                .where(InstanceType.provider_id == provider.id)
            )
        ).scalar_one()
        region_count = (
            await db.execute(
                select(func.count()).select_from(Region).where(Region.provider_id == provider.id)
            )
        ).scalar_one()
        return {"exists": True, "instance_types": it_count, "prices": price_count, "regions": region_count}


# ---------------------------------------------------------------------------
# Config-based providers (no auth, no network)
# ---------------------------------------------------------------------------


class TestConfigBasedIngestion:
    """Test config-based connectors that need no auth or network calls."""

    @pytest.mark.asyncio
    async def test_ingest_contabo(self):
        from connectors.contabo import ingest

        async with AsyncSessionLocal() as db:
            await ingest(db, only_eu=True, purchase_model="on_demand")
            await db.commit()

        stats = await _provider_stats("contabo")
        assert stats["exists"]
        assert stats["instance_types"] == 9
        assert stats["regions"] >= 2
        assert stats["prices"] > 0

    @pytest.mark.asyncio
    async def test_ingest_gcore(self):
        from connectors.gcore import ingest

        async with AsyncSessionLocal() as db:
            await ingest(db, only_eu=True, purchase_model="on_demand")
            await db.commit()

        stats = await _provider_stats("gcore")
        assert stats["exists"]
        assert stats["instance_types"] == 10
        assert stats["regions"] >= 8
        assert stats["prices"] >= 80

    @pytest.mark.asyncio
    async def test_ingest_cherry_servers(self):
        from connectors.cherry_servers import ingest

        async with AsyncSessionLocal() as db:
            await ingest(db, only_eu=True, purchase_model="on_demand")
            await db.commit()

        stats = await _provider_stats("cherry_servers")
        assert stats["exists"]
        assert stats["instance_types"] == 6
        assert stats["regions"] == 4
        assert stats["prices"] >= 24

    @pytest.mark.asyncio
    async def test_ingest_clouding(self):
        from connectors.clouding import ingest

        async with AsyncSessionLocal() as db:
            await ingest(db, only_eu=True, purchase_model="on_demand")
            await db.commit()

        stats = await _provider_stats("clouding")
        assert stats["exists"]
        assert stats["instance_types"] == 8
        assert stats["regions"] == 1
        assert stats["prices"] >= 8

    @pytest.mark.asyncio
    async def test_ingest_exoscale(self):
        from connectors.exoscale import ingest

        async with AsyncSessionLocal() as db:
            await ingest(db, only_eu=True, purchase_model="on_demand")
            await db.commit()

        stats = await _provider_stats("exoscale")
        assert stats["exists"]
        assert stats["instance_types"] == 23
        assert stats["regions"] >= 3
        assert stats["prices"] >= 69

    @pytest.mark.asyncio
    async def test_ingest_aruba(self):
        from connectors.aruba import ingest

        async with AsyncSessionLocal() as db:
            await ingest(db, only_eu=True, purchase_model="on_demand")
            await db.commit()

        stats = await _provider_stats("aruba")
        assert stats["exists"]
        assert stats["instance_types"] == 13
        assert stats["regions"] == 3
        assert stats["prices"] >= 39


# ---------------------------------------------------------------------------
# Public API providers (no auth, real network calls)
# ---------------------------------------------------------------------------


class TestPublicApiIngestion:
    """Test providers with public (unauthenticated) pricing APIs."""

    @pytest.mark.asyncio
    async def test_ingest_azure_quick(self):
        """Azure Retail Prices API — single region (westeurope)."""
        from connectors.azure import ingest

        async with AsyncSessionLocal() as db:
            await ingest(db, only_eu=True, purchase_model="on_demand", region="westeurope")
            await db.commit()

        stats = await _provider_stats("azure")
        assert stats["exists"]
        assert stats["instance_types"] > 100
        assert stats["prices"] > 100

    @pytest.mark.asyncio
    async def test_ingest_ovh(self):
        """OVH public catalog — all regions."""
        from connectors.ovh import ingest

        async with AsyncSessionLocal() as db:
            await ingest(db, only_eu=False, purchase_model="on_demand")
            await db.commit()

        stats = await _provider_stats("ovh")
        assert stats["exists"]
        assert stats["instance_types"] > 10
        assert stats["regions"] > 5
        assert stats["prices"] > 50

    @pytest.mark.asyncio
    async def test_ingest_scaleway(self):
        """Scaleway public Product Catalog API."""
        from connectors.scaleway import ingest

        async with AsyncSessionLocal() as db:
            await ingest(db, only_eu=False, purchase_model="on_demand")
            await db.commit()

        stats = await _provider_stats("scaleway")
        assert stats["exists"]
        assert stats["instance_types"] > 10
        assert stats["regions"] >= 9
        assert stats["prices"] > 50

    @pytest.mark.asyncio
    async def test_ingest_aws_quick(self):
        """AWS public bulk JSON — single region (eu-west-1)."""
        from connectors.aws import ingest

        async with AsyncSessionLocal() as db:
            await ingest(db, only_eu=True, purchase_model="on_demand", region="eu-west-1")
            await db.commit()

        stats = await _provider_stats("aws")
        assert stats["exists"]
        assert stats["instance_types"] > 100
        assert stats["prices"] > 100


# ---------------------------------------------------------------------------
# Cost component ingestion
# ---------------------------------------------------------------------------


class TestCostComponentIngestion:
    """Test cost component ingestion (egress, storage, public IP, OS license)."""

    @pytest.mark.asyncio
    async def test_ingest_egress_from_config(self):
        from connectors.egress_config import get_config_providers, ingest_egress_from_config

        providers = get_config_providers()
        assert len(providers) > 5

        async with AsyncSessionLocal() as db:
            for slug in providers[:3]:
                await ingest_egress_from_config(db, slug)
            await db.commit()

        count = await _count(EgressRate)
        assert count >= 3

    @pytest.mark.asyncio
    async def test_ingest_storage_from_config(self):
        from connectors.storage_config import get_storage_config_providers, ingest_storage_from_config

        providers = get_storage_config_providers()
        assert len(providers) >= 1

        async with AsyncSessionLocal() as db:
            for slug in providers:
                await ingest_storage_from_config(db, slug)
            await db.commit()

        count = await _count(StorageRate)
        assert count >= 1

    @pytest.mark.asyncio
    async def test_ingest_public_ip_from_config(self):
        from connectors.public_ip_config import get_public_ip_config_providers, ingest_public_ip_from_config

        providers = get_public_ip_config_providers()
        assert len(providers) >= 1

        async with AsyncSessionLocal() as db:
            for slug in providers:
                await ingest_public_ip_from_config(db, slug)
            await db.commit()

        count = await _count(PublicIpRate)
        assert count >= 1

    @pytest.mark.asyncio
    async def test_ingest_os_license_from_config(self):
        from connectors.os_license_config import get_os_license_config_providers, ingest_os_license_from_config

        providers = get_os_license_config_providers()
        assert len(providers) >= 1

        async with AsyncSessionLocal() as db:
            for slug in providers:
                await ingest_os_license_from_config(db, slug)
            await db.commit()

        count = await _count(OsLicenseRate)
        assert count >= 1

    @pytest.mark.asyncio
    async def test_ingest_egress_azure_public(self):
        """Azure egress via public Retail Prices API (no auth)."""
        from connectors.egress_azure import ingest_egress_azure

        async with AsyncSessionLocal() as db:
            await ingest_egress_azure(db)
            await db.commit()

        async with AsyncSessionLocal() as db:
            rate = (
                await db.execute(select(EgressRate).where(EgressRate.provider_slug == "azure").limit(1))
            ).scalars().first()
            assert rate is not None
            assert rate.egress_eur_per_gb > 0

    @pytest.mark.asyncio
    async def test_ingest_storage_azure_public(self):
        """Azure storage via public Retail Prices API (no auth)."""
        from connectors.storage_azure import ingest_storage_azure

        async with AsyncSessionLocal() as db:
            await ingest_storage_azure(db)
            await db.commit()

        async with AsyncSessionLocal() as db:
            count = (
                await db.execute(
                    select(func.count()).select_from(StorageRate).where(StorageRate.provider_slug == "azure")
                )
            ).scalar_one()
            assert count >= 1


# ---------------------------------------------------------------------------
# Auth-required providers (skipped if no credentials)
# ---------------------------------------------------------------------------


class TestAuthRequiredIngestion:
    """Test auth-required providers. Skipped if credentials are not configured."""

    @pytest.mark.asyncio
    async def test_ingest_hetzner(self):
        from app.core.config import settings

        if not settings.hetzner_api_token:
            pytest.skip("HETZNER_API_TOKEN not set")

        from connectors.hetzner import ingest

        async with AsyncSessionLocal() as db:
            await ingest(db, only_eu=True, purchase_model="on_demand")
            await db.commit()

        stats = await _provider_stats("hetzner")
        assert stats["exists"]
        assert stats["instance_types"] > 10
        assert stats["prices"] > 20

    @pytest.mark.asyncio
    async def test_ingest_digitalocean(self):
        from app.core.config import settings

        if not settings.digitalocean_api_token:
            pytest.skip("DIGITALOCEAN_API_TOKEN not set")

        from connectors.digitalocean import ingest

        async with AsyncSessionLocal() as db:
            await ingest(db, only_eu=True, purchase_model="on_demand")
            await db.commit()

        stats = await _provider_stats("digitalocean")
        assert stats["exists"]
        assert stats["instance_types"] > 5
        assert stats["prices"] > 10

    @pytest.mark.asyncio
    async def test_ingest_seeweb(self):
        from app.core.config import settings

        if not settings.seeweb_api_token and not (settings.seeweb_username and settings.seeweb_password):
            pytest.skip("SEEWEB credentials not set")

        from connectors.seeweb import ingest

        async with AsyncSessionLocal() as db:
            await ingest(db, only_eu=True, purchase_model="on_demand")
            await db.commit()

        stats = await _provider_stats("seeweb")
        assert stats["exists"]
        assert stats["instance_types"] > 5

    @pytest.mark.asyncio
    async def test_ingest_upcloud(self):
        from app.core.config import settings

        if not settings.upcloud_api_token and not (settings.upcloud_username and settings.upcloud_password):
            pytest.skip("UPCLOUD credentials not set")

        from connectors.upcloud import ingest

        try:
            async with AsyncSessionLocal() as db:
                await ingest(db, only_eu=True, purchase_model="on_demand")
                await db.commit()
        except (RuntimeError, Exception) as e:
            if "401" in str(e) or "Unauthorized" in str(e) or "authentication" in str(e).lower():
                pytest.skip(f"UpCloud auth failed (token may be expired): {e}")
            raise

        stats = await _provider_stats("upcloud")
        assert stats["exists"]
        assert stats["instance_types"] > 5


# ---------------------------------------------------------------------------
# Normalization verification
# ---------------------------------------------------------------------------


class TestNormalizationVerification:
    """Verify that ingested data follows normalization conventions."""

    @pytest.mark.asyncio
    async def test_family_taxonomy_is_normalized(self):
        """Family values from config-based providers should use the common taxonomy."""
        allowed_families = {
            "general-purpose",
            "compute-optimized",
            "memory-optimized",
            "storage-optimized",
            "gpu",
            None,
        }
        # Check only config-based providers (guaranteed to have normalized families)
        config_slugs = ["contabo", "gcore", "cherry_servers", "clouding", "exoscale", "aruba"]
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(InstanceType.family)
                .distinct()
                .join(Provider, InstanceType.provider_id == Provider.id)
                .where(Provider.slug.in_(config_slugs))
            )
            families = {r[0] for r in result.all()}

        unexpected = families - allowed_families
        assert not unexpected, f"Non-standard families found: {unexpected}"

    @pytest.mark.asyncio
    async def test_arch_values_are_canonical(self):
        """All arch values should be x86_64 or arm64."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(InstanceType.arch).distinct().where(InstanceType.arch.isnot(None))
            )
            arches = {r[0] for r in result.all()}

        assert arches <= {"x86_64", "arm64"}, f"Non-canonical arch values: {arches}"

    @pytest.mark.asyncio
    async def test_cpu_type_values_are_valid(self):
        """cpu_type should be shared, dedicated, or None."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(InstanceType.cpu_type).distinct().where(InstanceType.cpu_type.isnot(None))
            )
            types = {r[0] for r in result.all()}

        assert types <= {"shared", "dedicated"}, f"Invalid cpu_type values: {types}"

    @pytest.mark.asyncio
    async def test_prices_are_positive(self):
        """All prices should be > 0."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(func.count()).select_from(Price).where(Price.price_eur_per_hour <= 0)
            )
            bad_count = result.scalar_one()

        assert bad_count == 0, f"{bad_count} prices have non-positive EUR values"

    @pytest.mark.asyncio
    async def test_all_providers_have_regions(self):
        """Every provider should have at least one region."""
        async with AsyncSessionLocal() as db:
            providers = (await db.execute(select(Provider))).scalars().all()
            for p in providers:
                region_count = (
                    await db.execute(
                        select(func.count()).select_from(Region).where(Region.provider_id == p.id)
                    )
                ).scalar_one()
                assert region_count > 0, f"Provider {p.slug} has no regions"
