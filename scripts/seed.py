#!/usr/bin/env python3
"""Seed the database with providers, regions, instance types, and pricing data.

Run from project root: python scripts/seed.py
With Docker: docker compose exec api python scripts/seed.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal
from app.models import InstanceType, Price, Provider, Region
from app.models.price import PurchaseModel
from app.models.provider import ProviderType


async def get_or_create_provider(session, slug: str, name: str, website: str, provider_type: ProviderType) -> Provider:
    """Get existing provider or create a new one."""
    result = await session.execute(select(Provider).where(Provider.slug == slug))
    provider = result.scalar_one_or_none()
    if provider:
        return provider
    provider = Provider(name=name, slug=slug, website=website, type=provider_type)
    session.add(provider)
    await session.flush()
    return provider


async def get_or_create_region(session, provider_id: int, slug: str, name: str, country: str, is_eu: bool) -> Region:
    """Get existing region or create a new one."""
    result = await session.execute(
        select(Region).where(Region.provider_id == provider_id, Region.slug == slug)
    )
    region = result.scalar_one_or_none()
    if region:
        return region
    region = Region(provider_id=provider_id, name=name, slug=slug, country=country, is_eu=is_eu)
    session.add(region)
    await session.flush()
    return region


async def get_or_create_instance_type(
    session, provider_id: int, name: str, family: str, generation: str, vcpu: int, ram_gb: float, arch: str
) -> InstanceType:
    """Get existing instance type or create a new one."""
    result = await session.execute(
        select(InstanceType).where(InstanceType.provider_id == provider_id, InstanceType.name == name)
    )
    instance_type = result.scalar_one_or_none()
    if instance_type:
        return instance_type
    instance_type = InstanceType(
        provider_id=provider_id, name=name, family=family, generation=generation, vcpu=vcpu, ram_gb=ram_gb, arch=arch
    )
    session.add(instance_type)
    await session.flush()
    return instance_type


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        # 3 providers (get or create)
        aws = await get_or_create_provider(
            session, "aws", "Amazon Web Services", "https://aws.amazon.com", ProviderType.HYPERSCALER
        )
        gcp = await get_or_create_provider(
            session, "gcp", "Google Cloud Platform", "https://cloud.google.com", ProviderType.HYPERSCALER
        )
        ovh = await get_or_create_provider(
            session, "ovh", "OVHcloud", "https://www.ovhcloud.com", ProviderType.EU
        )

        # 5 regions (2 EU, 3 non-EU) - get or create
        regions = [
            await get_or_create_region(session, aws.id, "us-east-1", "US East (N. Virginia)", "US", False),
            await get_or_create_region(session, aws.id, "eu-west-1", "EU (Ireland)", "IE", True),
            await get_or_create_region(session, gcp.id, "europe-west1", "europe-west1", "BE", True),
            await get_or_create_region(session, gcp.id, "us-central1", "us-central1", "US", False),
            await get_or_create_region(session, ovh.id, "gra", "EU (GRA)", "FR", True),
        ]

        # 20 instance types (mix of vCPU, ram, arch) across providers
        # AWS: t3/t4g (x86/arm), GCP: n1/n2 (x86), e2 (x86/arm), OVH: b2
        instance_types = [
            # AWS
            await get_or_create_instance_type(session, aws.id, "t3.small", "t3", "3", 2, 2.0, "x86_64"),
            await get_or_create_instance_type(session, aws.id, "t3.medium", "t3", "3", 2, 4.0, "x86_64"),
            await get_or_create_instance_type(session, aws.id, "t3.large", "t3", "3", 2, 8.0, "x86_64"),
            await get_or_create_instance_type(session, aws.id, "t3.xlarge", "t3", "3", 4, 16.0, "x86_64"),
            await get_or_create_instance_type(session, aws.id, "t4g.small", "t4g", "4", 2, 2.0, "arm64"),
            await get_or_create_instance_type(session, aws.id, "t4g.medium", "t4g", "4", 2, 4.0, "arm64"),
            await get_or_create_instance_type(session, aws.id, "t4g.large", "t4g", "4", 2, 8.0, "arm64"),
            await get_or_create_instance_type(session, aws.id, "m5.large", "m5", "5", 2, 8.0, "x86_64"),
            await get_or_create_instance_type(session, aws.id, "m5.xlarge", "m5", "5", 4, 16.0, "x86_64"),
            # GCP
            await get_or_create_instance_type(session, gcp.id, "n1-standard-1", "n1", "1", 1, 3.75, "x86_64"),
            await get_or_create_instance_type(session, gcp.id, "n1-standard-2", "n1", "1", 2, 7.5, "x86_64"),
            await get_or_create_instance_type(session, gcp.id, "n1-standard-4", "n1", "1", 4, 15.0, "x86_64"),
            await get_or_create_instance_type(session, gcp.id, "e2-medium", "e2", "2", 1, 4.0, "x86_64"),
            await get_or_create_instance_type(session, gcp.id, "e2-standard-2", "e2", "2", 2, 8.0, "x86_64"),
            await get_or_create_instance_type(session, gcp.id, "e2-standard-4", "e2", "2", 4, 16.0, "x86_64"),
            await get_or_create_instance_type(session, gcp.id, "t2a-standard-1", "t2a", "2", 1, 4.0, "arm64"),
            await get_or_create_instance_type(session, gcp.id, "t2a-standard-2", "t2a", "2", 2, 8.0, "arm64"),
            # OVH
            await get_or_create_instance_type(session, ovh.id, "b2-7", "b2", "2", 4, 7.0, "x86_64"),
            await get_or_create_instance_type(session, ovh.id, "b2-15", "b2", "2", 4, 15.0, "x86_64"),
            await get_or_create_instance_type(session, ovh.id, "b2-30", "b2", "2", 8, 30.0, "x86_64"),
        ]

        # Build region lookup by (provider_id, slug)
        region_by_provider_slug = {}
        for r in regions:
            region_by_provider_slug[(r.provider_id, r.slug)] = r

        # Prices: on_demand (and some spot) in EUR for each (instance_type, region) of same provider
        # Check if prices already exist before adding
        prices = []
        # Price per hour in EUR (example rates)
        default_rates_eur = [
            0.04, 0.08, 0.16, 0.32, 0.04, 0.08, 0.16, 0.12, 0.24,  # AWS 9
            0.03, 0.06, 0.12, 0.04, 0.08, 0.16, 0.04, 0.08,       # GCP 8
            0.05, 0.09, 0.18,                                       # OVH 3
        ]
        for it, rate in zip(instance_types, default_rates_eur, strict=True):
            # Get regions for this provider
            provider_regions = [r for r in regions if r.provider_id == it.provider_id]
            for reg in provider_regions:
                # Slight variation by region (EU sometimes +10%)
                mult = 1.1 if reg.is_eu else 1.0
                p_eur = round(rate * mult, 4)
                
                # Check if price already exists for on_demand (use first() instead of scalar_one_or_none() to handle multiple)
                existing_on_demand_result = await session.execute(
                    select(Price).where(
                        Price.instance_type_id == it.id,
                        Price.region_id == reg.id,
                        Price.purchase_model == PurchaseModel.ON_DEMAND,
                    )
                )
                existing_on_demand = existing_on_demand_result.scalars().first()
                if not existing_on_demand:
                    prices.append(
                        Price(
                            instance_type_id=it.id,
                            region_id=reg.id,
                            purchase_model=PurchaseModel.ON_DEMAND,
                            currency="EUR",
                            price_per_hour=p_eur,
                        )
                    )
                
                # Check if price already exists for spot
                existing_spot_result = await session.execute(
                    select(Price).where(
                        Price.instance_type_id == it.id,
                        Price.region_id == reg.id,
                        Price.purchase_model == PurchaseModel.SPOT,
                    )
                )
                existing_spot = existing_spot_result.scalars().first()
                if not existing_spot:
                    prices.append(
                        Price(
                            instance_type_id=it.id,
                            region_id=reg.id,
                            purchase_model=PurchaseModel.SPOT,
                            currency="EUR",
                            price_per_hour=round(p_eur * 0.4, 4),
                        )
                    )

        if prices:
            session.add_all(prices)
        await session.commit()
        # Logging will be initialized if app.core.logging is imported
        # For standalone script, use print or initialize logging
        print(
            f"Seeded: {3} providers, {len(regions)} regions, {len(instance_types)} instance types, {len(prices)} prices."
        )


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
