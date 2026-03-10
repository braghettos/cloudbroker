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

logger = get_logger(__name__)


def _parse() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="CloudBroker ingestion CLI")
    sub = p.add_subparsers(dest="command", required=True)
    ing = sub.add_parser("ingest", help="Ingest pricing from provider(s)")
    ing.add_argument(
        "--provider",
        choices=["gcp", "hetzner", "aws", "azure", "digitalocean", "scaleway", "ovh", "aruba", "upcloud", "open_telekom_cloud", "exoscale", "ionos", "gridscale", "stackit", "elastx", "cyso_cloud", "seeweb", "all"],
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
    ing.add_argument(
        "--verify",
        action="store_true",
        help="After GCP ingestion, verify stored prices match the Billing Catalog API.",
    )
    ing.add_argument(
        "--verify-fail",
        action="store_true",
        help="With --verify: raise on verification failure (default: log and continue).",
    )
    return p


PROVIDERS_ALL = [
    "azure",
    "aws",
    "gcp",
    "hetzner",
    "digitalocean",
    "scaleway",
    "ovh",
    "aruba",
    "upcloud",
    "open_telekom_cloud",
    "exoscale",
    "ionos",
    "gridscale",
    "stackit",
    "elastx",
    "cyso_cloud",
    "seeweb",
]


def _get_providers_to_ingest(provider: str) -> list[str]:
    """Return list of provider slugs to ingest."""
    return [provider] if provider != "all" else list(PROVIDERS_ALL)


async def _ingest_provider_instance(
    slug: str,
    only_eu: bool,
    purchase_model: str,
    region: str | None,
) -> None:
    """Run instance ingestion for a single provider with its own DB session."""
    from connectors import aruba as aruba_conn
    from connectors import aws as aws_conn
    from connectors import azure as azure_conn
    from connectors import digitalocean as digitalocean_conn
    from connectors import gcp as gcp_conn
    from connectors import gcp_machine_types as gcp_machines_conn
    from connectors import hetzner as hetzner_conn
    from connectors import ovh as ovh_conn
    from connectors import scaleway as scaleway_conn
    from connectors import upcloud as upcloud_conn
    from connectors import open_telekom_cloud as otc_conn
    from connectors import exoscale as exoscale_conn
    from connectors import ionos as ionos_conn
    from connectors import gridscale as gridscale_conn
    from connectors import stackit as stackit_conn
    from connectors import elastx as elastx_conn
    from connectors import cyso_cloud as cyso_cloud_conn
    from connectors import seeweb as seeweb_conn

    conn_map = {
        "aruba": aruba_conn,
        "aws": aws_conn,
        "azure": azure_conn,
        "digitalocean": digitalocean_conn,
        "gcp": gcp_conn,
        "hetzner": hetzner_conn,
        "ovh": ovh_conn,
        "scaleway": scaleway_conn,
        "upcloud": upcloud_conn,
        "open_telekom_cloud": otc_conn,
        "exoscale": exoscale_conn,
        "ionos": ionos_conn,
        "gridscale": gridscale_conn,
        "stackit": stackit_conn,
        "elastx": elastx_conn,
        "cyso_cloud": cyso_cloud_conn,
        "seeweb": seeweb_conn,
    }
    conn = conn_map.get(slug)
    if not conn:
        return

    async with AsyncSessionLocal() as db:
        try:
            if slug == "aws":
                await aws_conn.ingest(db, only_eu=only_eu, purchase_model=purchase_model, region=region)
            elif slug == "azure":
                await azure_conn.ingest(db, only_eu=only_eu, purchase_model=purchase_model, region=region)
            else:
                await conn.ingest(db, only_eu=only_eu, purchase_model=purchase_model)
            if slug == "gcp":
                await gcp_machines_conn.ingest(db, only_eu=only_eu, purchase_model=purchase_model)
            await db.commit()
            logger.info(f"{slug} ingestion completed successfully")
        except Exception as e:
            logger.error(f"{slug} ingestion failed: {e}", exc_info=True)
            raise


async def _ingest_egress_for_provider(slug: str) -> None:
    """Run egress ingestion for a single provider with its own DB session."""
    from connectors import egress_azure as egress_azure_conn
    from connectors import egress_aws as egress_aws_conn
    from connectors import egress_config as egress_config_module
    from connectors import egress_gcp as egress_gcp_conn

    config_providers = set(egress_config_module.get_config_providers())
    if slug not in ("azure", "aws", "gcp") and slug not in config_providers:
        return

    async with AsyncSessionLocal() as db:
        try:
            if slug == "azure":
                await egress_azure_conn.ingest_egress_azure(db)
            elif slug == "aws":
                await egress_aws_conn.ingest_egress_aws(db)
            elif slug == "gcp":
                await egress_gcp_conn.ingest_egress_gcp(db)
            elif slug in config_providers:
                await egress_config_module.ingest_egress_from_config(db, slug)
            await db.commit()
            logger.info(f"{slug} egress rate ingestion completed successfully")
        except Exception as e:
            logger.error(f"{slug} egress ingestion failed: {e}", exc_info=True)


async def _ingest_storage_for_provider(slug: str) -> None:
    """Run storage ingestion for a single provider with its own DB session."""
    from connectors import storage_aws as storage_aws_conn
    from connectors import storage_azure as storage_azure_conn
    from connectors import storage_config as storage_config_module
    from connectors import storage_gcp as storage_gcp_conn

    config_providers = set(storage_config_module.get_storage_config_providers())
    if slug not in ("aws", "azure", "gcp") and slug not in config_providers:
        return

    async with AsyncSessionLocal() as db:
        try:
            if slug == "aws":
                await storage_aws_conn.ingest_storage_aws(db)
            elif slug == "azure":
                await storage_azure_conn.ingest_storage_azure(db)
            elif slug == "gcp":
                await storage_gcp_conn.ingest_storage_gcp(db)
            elif slug in config_providers:
                await storage_config_module.ingest_storage_from_config(db, slug)
            await db.commit()
            logger.info(f"{slug} storage rate ingestion completed successfully")
        except Exception as e:
            logger.error(f"{slug} storage ingestion failed: {e}", exc_info=True)


async def _ingest_public_ip_for_provider(slug: str) -> None:
    """Run public IP ingestion for a single provider with its own DB session."""
    from connectors import public_ip_config as public_ip_config_module

    config_providers = set(public_ip_config_module.get_public_ip_config_providers())
    if slug not in config_providers:
        return

    async with AsyncSessionLocal() as db:
        try:
            await public_ip_config_module.ingest_public_ip_from_config(db, slug)
            await db.commit()
            logger.info(f"{slug} public IP rate ingestion completed successfully")
        except Exception as e:
            logger.error(f"{slug} public IP ingestion failed: {e}", exc_info=True)


async def _ingest_os_license_for_provider(slug: str) -> None:
    """Run OS license ingestion for a single provider with its own DB session."""
    from connectors import os_license_config as os_license_config_module

    config_providers = set(os_license_config_module.get_os_license_config_providers())
    if slug not in config_providers:
        return

    async with AsyncSessionLocal() as db:
        try:
            await os_license_config_module.ingest_os_license_from_config(db, slug)
            await db.commit()
            logger.info(f"{slug} OS license rate ingestion completed successfully")
        except Exception as e:
            logger.error(f"{slug} OS license ingestion failed: {e}", exc_info=True)


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
    verify: bool = False,
    verify_fail: bool = False,
) -> None:
    if quick and region is None:
        region = "eu-west-1" if provider == "aws" else ("westeurope" if provider == "azure" else None)

    skip_instances = egress_only or costs_only

    # Parallel instance ingestion: each provider runs in its own session
    if not skip_instances:
        providers = _get_providers_to_ingest(provider)
        tasks = [
            _ingest_provider_instance(slug, only_eu, purchase_model, region if slug in ("aws", "azure") else None)
            for slug in providers
        ]
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error(f"{providers[i]} ingestion failed: {r}")

    # Parallel cost ingestion: each provider runs in its own session
    cost_providers = _get_providers_to_ingest("all" if (egress_only or costs_only) else provider)
    cost_tasks: list = []
    cost_task_info: list[tuple[str, str]] = []  # (cost_type, slug)

    if egress or all_costs or egress_only:
        cost_tasks += [_ingest_egress_for_provider(slug) for slug in cost_providers]
        cost_task_info += [("egress", slug) for slug in cost_providers]
    if storage or all_costs:
        cost_tasks += [_ingest_storage_for_provider(slug) for slug in cost_providers]
        cost_task_info += [("storage", slug) for slug in cost_providers]
    if public_ip or all_costs:
        cost_tasks += [_ingest_public_ip_for_provider(slug) for slug in cost_providers]
        cost_task_info += [("public_ip", slug) for slug in cost_providers]
    if os_license or all_costs:
        cost_tasks += [_ingest_os_license_for_provider(slug) for slug in cost_providers]
        cost_task_info += [("os_license", slug) for slug in cost_providers]

    if cost_tasks:
        cost_results = await asyncio.gather(*cost_tasks, return_exceptions=True)
        for (cost_type, slug), r in zip(cost_task_info, cost_results):
            if isinstance(r, Exception):
                logger.error(f"{slug} {cost_type} ingestion failed: {r}", exc_info=True)

    if verify and not skip_instances:
        providers_ingested = _get_providers_to_ingest(provider)
        if "gcp" in providers_ingested:
            from connectors import gcp_verify

            async with AsyncSessionLocal() as db:
                try:
                    results = await gcp_verify.verify_ingested_prices(
                        db,
                        only_eu=only_eu,
                        purchase_model=purchase_model,
                        fail_on_mismatch=verify_fail,
                    )
                    for r in results:
                        if r.passed:
                            logger.info("GCP verify: %s", r.message)
                        else:
                            logger.error("GCP verify FAIL: %s", r.message)
                            if r.details:
                                logger.error("Details: %s", r.details)
                except RuntimeError as e:
                    logger.error("GCP verification failed: %s", e)
                    raise

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
            verify=args.verify,
            verify_fail=args.verify_fail,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
