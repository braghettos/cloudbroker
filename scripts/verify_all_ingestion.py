#!/usr/bin/env python3
"""
Verify ingested prices for all providers.

Runs verification for each provider that supports it. Providers without credentials
are skipped. Use --fail to exit with error code 1 if any verification fails.

Usage:
  python scripts/verify_all_ingestion.py [--fail]
  make verify-all  # via Makefile
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass


VERIFY_MODULES = [
    ("GCP", "connectors.gcp_verify"),
    ("Hetzner", "connectors.hetzner_verify"),
    ("Azure", "connectors.azure_verify"),
    ("Open Telekom Cloud", "connectors.otc_verify"),
    ("OVH", "connectors.ovh_verify"),
    ("DigitalOcean", "connectors.digitalocean_verify"),
    ("AWS", "connectors.aws_verify"),
    ("Scaleway", "connectors.scaleway_verify"),
    ("UpCloud", "connectors.upcloud_verify"),
    ("Seeweb", "connectors.seeweb_verify"),
    ("Config (Aruba, Exoscale, IONOS, gridscale, STACKIT, Elastx, Cyso)", "connectors.config_verify"),
]


async def main() -> int:
    parser = argparse.ArgumentParser(description="Verify ingested prices for all providers")
    parser.add_argument(
        "--fail",
        action="store_true",
        help="Exit with error code 1 on any verification failure",
    )
    parser.add_argument(
        "--only-eu",
        type=lambda x: x.lower() in ("1", "true", "yes"),
        default=True,
        help="Verify only EU regions (default: true)",
    )
    parser.add_argument(
        "--provider",
        choices=["gcp", "hetzner", "azure", "otc", "ovh", "digitalocean", "aws", "scaleway", "upcloud", "seeweb", "config"],
        help="Verify only this provider (default: all)",
    )
    args = parser.parse_args()

    from app.core.database import AsyncSessionLocal
    from connectors.hetzner_verify import VerificationResult

    provider_filter = {
        "gcp": ("GCP", "connectors.gcp_verify"),
        "hetzner": ("Hetzner", "connectors.hetzner_verify"),
        "azure": ("Azure", "connectors.azure_verify"),
        "otc": ("Open Telekom Cloud", "connectors.otc_verify"),
        "ovh": ("OVH", "connectors.ovh_verify"),
        "digitalocean": ("DigitalOcean", "connectors.digitalocean_verify"),
        "aws": ("AWS", "connectors.aws_verify"),
        "scaleway": ("Scaleway", "connectors.scaleway_verify"),
        "upcloud": ("UpCloud", "connectors.upcloud_verify"),
        "seeweb": ("Seeweb", "connectors.seeweb_verify"),
        "config": ("Config (Aruba, Exoscale, IONOS, gridscale, STACKIT, Elastx, Cyso)", "connectors.config_verify"),
    }
    modules_to_run = [provider_filter[args.provider]] if args.provider else VERIFY_MODULES

    all_results: list[tuple[str, list]] = []

    async with AsyncSessionLocal() as db:
        for name, module_path in modules_to_run:
            try:
                mod = importlib.import_module(module_path)
                results = await mod.verify_ingested_prices(
                    db,
                    only_eu=args.only_eu,
                    purchase_model="on_demand",
                    fail_on_mismatch=False,
                )
                all_results.append((name, results))
            except Exception as e:
                all_results.append((name, [VerificationResult(passed=False, message=str(e), details=None)]))

    failed_count = 0
    for provider, results in all_results:
        print(f"\n=== {provider} ===")
        for r in results:
            status = "PASS" if r.passed else "FAIL"
            print(f"  [{status}] {r.message}")
            if getattr(r, "details", None) and not r.passed:
                for k, v in r.details.items():
                    print(f"    {k}: {v}")
            if not r.passed:
                failed_count += 1

    if failed_count > 0:
        print(f"\n{failed_count} verification(s) failed.")
        return 1 if args.fail else 0
    print("\nAll verifications passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
