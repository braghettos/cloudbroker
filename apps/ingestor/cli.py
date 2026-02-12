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
from app.core.fx import ensure_stub_fx_rates
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
        choices=["gcp", "hetzner", "aws", "azure", "digitalocean", "scaleway", "ovh", "aruba", "all"],
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
    return p


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
) -> None:
    from connectors import aruba as aruba_conn
    from connectors import aws as aws_conn
    from connectors import azure as azure_conn
    from connectors import digitalocean as digitalocean_conn
    from connectors import gcp as gcp_conn
    from connectors import hetzner as hetzner_conn
    from connectors import ovh as ovh_conn
    from connectors import scaleway as scaleway_conn

    if quick and region is None:
        region = "eu-west-1" if provider == "aws" else ("westeurope" if provider == "azure" else None)

    async with AsyncSessionLocal() as db:
        await ensure_stub_fx_rates(db)
        await _ensure_ingestion_providers(db)
        await db.commit()

    async with AsyncSessionLocal() as db:
        # Ingest each provider independently, continuing on errors
        if provider in ("hetzner", "all"):
            try:
                await hetzner_conn.ingest(db, only_eu=only_eu, purchase_model=purchase_model)
                logger.info("Hetzner ingestion completed successfully")
            except Exception as e:
                logger.error(f"Hetzner ingestion failed: {e}", exc_info=True)
        if provider in ("gcp", "all"):
            try:
                await gcp_conn.ingest(db, only_eu=only_eu, purchase_model=purchase_model)
                logger.info("GCP ingestion completed successfully")
            except Exception as e:
                logger.error(f"GCP ingestion failed: {e}", exc_info=True)
        if provider in ("aws", "all"):
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
        if provider in ("azure", "all"):
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
        if provider in ("digitalocean", "all"):
            try:
                await digitalocean_conn.ingest(
                    db,
                    only_eu=only_eu,
                    purchase_model=purchase_model,
                )
                logger.info("DigitalOcean ingestion completed successfully")
            except Exception as e:
                logger.error(f"DigitalOcean ingestion failed: {e}", exc_info=True)
        if provider in ("scaleway", "all"):
            try:
                await scaleway_conn.ingest(
                    db,
                    only_eu=only_eu,
                    purchase_model=purchase_model,
                )
                logger.info("Scaleway ingestion completed successfully")
            except Exception as e:
                logger.error(f"Scaleway ingestion failed: {e}", exc_info=True)
        if provider in ("ovh", "all"):
            try:
                await ovh_conn.ingest(
                    db,
                    only_eu=only_eu,
                    purchase_model=purchase_model,
                )
                logger.info("OVH ingestion completed successfully")
            except Exception as e:
                logger.error(f"OVH ingestion failed: {e}", exc_info=True)
        if provider in ("aruba", "all"):
            try:
                await aruba_conn.ingest(
                    db,
                    only_eu=only_eu,
                    purchase_model=purchase_model,
                )
                logger.info("Aruba ingestion completed successfully")
            except Exception as e:
                logger.error(f"Aruba ingestion failed: {e}", exc_info=True)
        await db.commit()

    # After base GCP ingestion, compute machine type prices
    if provider in ("gcp", "all"):
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
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
