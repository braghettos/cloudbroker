"""CLI for ingestion: ingest --provider gcp|hetzner|all."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Project root on path
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

# Load .env so AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are available to boto3
try:
    from dotenv import load_dotenv

    load_dotenv(_project_root / ".env")
except ImportError:
    pass

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.models.provider import ProviderType
from app.services.ingestion import ensure_provider

logger = get_logger(__name__)


def _parse() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="CloudBroker ingestion CLI")
    sub = p.add_subparsers(dest="command", required=True)
    ing = sub.add_parser("ingest", help="Ingest pricing from provider(s)")
    ing.add_argument(
        "--provider",
        choices=["gcp", "hetzner", "aws", "azure", "digitalocean", "scaleway", "ovh", "all"],
        required=True,
        help="Provider to ingest",
    )
    ing.add_argument(
        "--only-eu",
        type=lambda x: x.lower() in ("1", "true", "yes"),
        default=False,
        metavar="BOOL",
        help="Restrict to EU regions only during ingestion (default: false - ingest all regions, filter in recommendations)",
    )
    ing.add_argument(
        "--purchase-model",
        choices=["on_demand", "spot", "reserved"],
        default="on_demand",
        help="Purchase model to ingest (default: on_demand)",
    )
    ing.add_argument(
        "--region",
        metavar="SLUG",
        default=None,
        help="Ingest only this region (e.g. eu-west-1). For AWS only; speeds up validation.",
    )
    ing.add_argument(
        "--quick",
        action="store_true",
        help="Ingest a single region for fast validation (AWS: eu-west-1, others: first region).",
    )
    ing.add_argument(
        "--egress",
        action="store_true",
        help="Also ingest egress rates (API: Azure/AWS/GCP; config: Hetzner/DO/Scaleway/OVH).",
    )
    ing.add_argument(
        "--egress-only",
        action="store_true",
        help="Ingest only egress rates for all 7 providers (no instance/price data).",
    )
    ing.add_argument(
        "--storage",
        action="store_true",
        help="Also ingest storage rates (API: AWS/Azure/GCP; config: Hetzner/DO/Scaleway/OVH).",
    )
    ing.add_argument(
        "--public-ip",
        action="store_true",
        help="Also ingest public IP rates (config for all providers).",
    )
    ing.add_argument(
        "--os-license",
        action="store_true",
        help="Also ingest OS license rates (config for all providers).",
    )
    ing.add_argument(
        "--all-costs",
        action="store_true",
        help="Ingest egress, storage, public IP, and OS license rates (convenience flag).",
    )
    ing.add_argument(
        "--costs-only",
        action="store_true",
        help="Skip instance/price ingestion; only ingest cost rates (egress, storage, etc.).",
    )
    return p


PROVIDERS_ALL = ["azure", "aws", "gcp", "hetzner", "digitalocean", "scaleway", "ovh"]


async def _run_egress_ingestion(db, provider: str) -> None:
    """Run egress rate ingestion for provider(s). API-based where possible, config otherwise."""
    from connectors import egress_azure as egress_azure_conn
    from connectors import egress_aws as egress_aws_conn
    from connectors import egress_config as egress_config_module
    from connectors import egress_gcp as egress_gcp_conn

    providers = [provider] if provider != "all" else PROVIDERS_ALL
    config_providers = set(egress_config_module.get_config_providers())

    for slug in providers:
        try:
            if slug == "azure":
                await egress_azure_conn.ingest_egress_azure(db)
            elif slug == "aws":
                await egress_aws_conn.ingest_egress_aws(db)
            elif slug == "gcp":
                await egress_gcp_conn.ingest_egress_gcp(db)
            elif slug in config_providers:
                await egress_config_module.ingest_egress_from_config(db, slug)
            else:
                continue
            logger.info(f"{slug} egress rate ingestion completed successfully")
        except Exception as e:
            logger.error(f"{slug} egress ingestion failed: {e}", exc_info=True)


async def _run_storage_ingestion(db, provider: str) -> None:
    """Run storage rate ingestion. API for AWS/Azure/GCP, config otherwise."""
    from connectors import storage_aws as storage_aws_conn
    from connectors import storage_azure as storage_azure_conn
    from connectors import storage_config as storage_config_module
    from connectors import storage_gcp as storage_gcp_conn

    providers = [provider] if provider != "all" else PROVIDERS_ALL
    config_providers = set(storage_config_module.get_storage_config_providers())

    for slug in providers:
        try:
            if slug == "aws":
                await storage_aws_conn.ingest_storage_aws(db)
            elif slug == "azure":
                await storage_azure_conn.ingest_storage_azure(db)
            elif slug == "gcp":
                await storage_gcp_conn.ingest_storage_gcp(db)
            elif slug in config_providers:
                await storage_config_module.ingest_storage_from_config(db, slug)
            else:
                continue
            logger.info(f"{slug} storage rate ingestion completed successfully")
        except Exception as e:
            logger.error(f"{slug} storage ingestion failed: {e}", exc_info=True)


async def _run_public_ip_ingestion(db, provider: str) -> None:
    """Run public IP rate ingestion from config."""
    from connectors import public_ip_config as public_ip_config_module

    providers = [provider] if provider != "all" else PROVIDERS_ALL
    config_providers = set(public_ip_config_module.get_public_ip_config_providers())

    for slug in providers:
        try:
            if slug in config_providers:
                await public_ip_config_module.ingest_public_ip_from_config(db, slug)
                logger.info(f"{slug} public IP rate ingestion completed successfully")
        except Exception as e:
            logger.error(f"{slug} public IP ingestion failed: {e}", exc_info=True)


async def _run_os_license_ingestion(db, provider: str) -> None:
    """Run OS license rate ingestion from config."""
    from connectors import os_license_config as os_license_config_module

    providers = [provider] if provider != "all" else PROVIDERS_ALL
    config_providers = set(os_license_config_module.get_os_license_config_providers())

    for slug in providers:
        try:
            if slug in config_providers:
                await os_license_config_module.ingest_os_license_from_config(db, slug)
                logger.info(f"{slug} OS license rate ingestion completed successfully")
        except Exception as e:
            logger.error(f"{slug} OS license ingestion failed: {e}", exc_info=True)


async def _ensure_ingestion_providers(db) -> None:
    """Ensure gcp and hetzner providers exist."""
    await ensure_provider(
        db,
        slug="gcp",
        name="Google Cloud",
        website="https://cloud.google.com",
        type=ProviderType.HYPERSCALER,
    )
    await ensure_provider(
        db,
        slug="hetzner",
        name="Hetzner Cloud",
        website="https://www.hetzner.com/cloud",
        type=ProviderType.EU,
    )
    await db.flush()


async def run_ingest(
    provider: str,
    only_eu: bool,
    purchase_model: str,
    region: str | None = None,
    quick: bool = False,
    egress: bool = False,
    egress_only: bool = False,
    storage: bool = False,
    public_ip: bool = False,
    os_license: bool = False,
    all_costs: bool = False,
    costs_only: bool = False,
) -> None:
    from connectors import aws as aws_conn
    from connectors import azure as azure_conn
    from connectors import digitalocean as digitalocean_conn
    from connectors import gcp as gcp_conn
    from connectors import hetzner as hetzner_conn
    from connectors import ovh as ovh_conn
    from connectors import scaleway as scaleway_conn

    if quick and region is None:
        region = "eu-west-1" if provider == "aws" else ("westeurope" if provider == "azure" else None)

    skip_instances = egress_only or costs_only

    async with AsyncSessionLocal() as db:
        await _ensure_ingestion_providers(db)
        await db.commit()

    async with AsyncSessionLocal() as db:
        # Ingest each provider independently, continuing on errors (skip when skip_instances)
        if not skip_instances and provider in ("hetzner", "all"):
            try:
                await hetzner_conn.ingest(db, only_eu=only_eu, purchase_model=purchase_model)
                logger.info("Hetzner ingestion completed successfully")
            except Exception as e:
                logger.error(f"Hetzner ingestion failed: {e}", exc_info=True)
        if not skip_instances and provider in ("gcp", "all"):
            try:
                await gcp_conn.ingest(db, only_eu=only_eu, purchase_model=purchase_model)
                logger.info("GCP ingestion completed successfully")
            except Exception as e:
                logger.error(f"GCP ingestion failed: {e}", exc_info=True)
        if not skip_instances and provider in ("aws", "all"):
            try:
                await aws_conn.ingest(
                    db,
                    only_eu=only_eu,
                    purchase_model=purchase_model,
                    region=region,
                )
                logger.info("AWS ingestion completed successfully")
            except Exception as e:
                logger.error(f"AWS ingestion failed: {e}", exc_info=True)
        if not skip_instances and provider in ("azure", "all"):
            try:
                await azure_conn.ingest(
                    db,
                    only_eu=only_eu,
                    purchase_model=purchase_model,
                    region=region,
                )
                logger.info("Azure ingestion completed successfully")
            except Exception as e:
                logger.error(f"Azure ingestion failed: {e}", exc_info=True)
        if not skip_instances and provider in ("digitalocean", "all"):
            try:
                await digitalocean_conn.ingest(
                    db,
                    only_eu=only_eu,
                    purchase_model=purchase_model,
                )
                logger.info("DigitalOcean ingestion completed successfully")
            except Exception as e:
                logger.error(f"DigitalOcean ingestion failed: {e}", exc_info=True)
        if not skip_instances and provider in ("scaleway", "all"):
            try:
                await scaleway_conn.ingest(
                    db,
                    only_eu=only_eu,
                    purchase_model=purchase_model,
                )
                logger.info("Scaleway ingestion completed successfully")
            except Exception as e:
                logger.error(f"Scaleway ingestion failed: {e}", exc_info=True)
        if not skip_instances and provider in ("ovh", "all"):
            try:
                await ovh_conn.ingest(
                    db,
                    only_eu=only_eu,
                    purchase_model=purchase_model,
                )
                logger.info("OVH ingestion completed successfully")
            except Exception as e:
                logger.error(f"OVH ingestion failed: {e}", exc_info=True)
        # Cost rate ingestion: egress, storage, public IP, OS license
        # When egress_only or costs_only, skip instance ingestion; cost provider = all
        cost_provider = "all" if (egress_only or costs_only) else provider
        if egress or all_costs or egress_only:
            await _run_egress_ingestion(db, cost_provider)
        if storage or all_costs:
            await _run_storage_ingestion(db, cost_provider)
        if public_ip or all_costs:
            await _run_public_ip_ingestion(db, cost_provider)
        if os_license or all_costs:
            await _run_os_license_ingestion(db, cost_provider)
        await db.commit()

    # After base GCP ingestion, compute machine type prices (skip when costs-only mode)
    if not skip_instances and provider in ("gcp", "all"):
        try:
            from connectors import gcp_machine_types as gcp_machines_conn

            async with AsyncSessionLocal() as db:
                await gcp_machines_conn.ingest(db, only_eu=only_eu, purchase_model=purchase_model)
                await db.commit()
            logger.info("GCP machine types ingestion completed successfully")
        except Exception as e:
            logger.error(f"GCP machine types ingestion failed: {e}", exc_info=True)

    logger.info(
        "Ingestion completed",
        provider=provider,
        only_eu=only_eu,
        purchase_model=purchase_model,
        region=region,
    )


def main() -> int:
    parser = _parse()
    args = parser.parse_args()
    if args.command != "ingest":
        return 0
    asyncio.run(
        run_ingest(
            args.provider,
            args.only_eu,
            args.purchase_model,
            region=args.region,
            quick=args.quick,
            egress=args.egress,
            egress_only=args.egress_only,
            storage=args.storage,
            public_ip=args.public_ip,
            os_license=args.os_license,
            all_costs=args.all_costs,
            costs_only=args.costs_only,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
