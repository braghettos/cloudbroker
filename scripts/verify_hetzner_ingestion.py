#!/usr/bin/env python3
"""
Verify Hetzner ingested prices match the Cloud API.

Run after ingestion to double-check stored values. Use --fail to exit with error on mismatch.
Requires HETZNER_API_TOKEN.

Usage:
  python scripts/verify_hetzner_ingestion.py [--fail]
  make verify-hetzner  # via Makefile
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass


async def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Hetzner ingested prices match API")
    parser.add_argument(
        "--fail",
        action="store_true",
        help="Exit with error code 1 on verification failure",
    )
    parser.add_argument(
        "--only-eu",
        type=lambda x: x.lower() in ("1", "true", "yes"),
        default=True,
        help="Verify only EU regions (default: true)",
    )
    args = parser.parse_args()

    from app.core.database import AsyncSessionLocal
    from connectors import hetzner_verify

    async with AsyncSessionLocal() as db:
        results = await hetzner_verify.verify_ingested_prices(
            db,
            only_eu=args.only_eu,
            purchase_model="on_demand",
            fail_on_mismatch=args.fail,
        )

    failed = [r for r in results if not r.passed]
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"[{status}] {r.message}")
        if r.details and not r.passed:
            for k, v in r.details.items():
                print(f"  {k}: {v}")

    if failed:
        print(f"\n{len(failed)} verification(s) failed.")
        return 1
    print("\nAll Hetzner verifications passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
