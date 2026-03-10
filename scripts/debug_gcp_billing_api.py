#!/usr/bin/env python3
"""Debug script: dump raw GCP Billing Catalog API response for Compute Engine CPU/RAM SKUs.

Run with: python scripts/debug_gcp_billing_api.py

This helps identify the exact usage_unit and price values returned by the API,
to fix unit conversion issues (e.g. per-minute vs per-hour).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Load .env
root = Path(__file__).resolve().parents[1]
env_file = root / ".env"
if env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(env_file)

sys.path.insert(0, str(root))


def _get_billing_client():
    try:
        from google.cloud import billing_v1
    except ImportError:
        raise RuntimeError("google-cloud-billing required: pip install google-cloud-billing")
    return billing_v1.CloudCatalogClient()


def _list_services(client) -> list:
    return list(client.list_services())


def _list_skus(client, service_name: str) -> list:
    return list(client.list_skus(parent=service_name))


def _is_compute_linux_vm_sku(sku) -> bool:
    try:
        cat = sku.category
        if not cat:
            return False
        rf = (cat.resource_family or "").upper()
        rg = (cat.resource_group or "").upper()
        if "COMPUTE" in rf and "RAM" in rg:
            return True
        if "COMPUTE" in rf and "CPU" in rg:
            return True
        return False
    except Exception:
        return False


def _is_preemptible_sku(sku) -> bool:
    try:
        cat = sku.category
        if not cat:
            return False
        ut = (cat.usage_type or "").upper()
        if "PREEMPTIBLE" in ut or "PREEMPT" in ut:
            return True
        desc = (sku.description or "").upper()
        if "PREEMPTIBLE" in desc or "PREEMPT" in desc:
            return True
        return False
    except Exception:
        return False


def _service_regions(sku) -> list[str]:
    try:
        return list(sku.service_regions) if sku.service_regions else []
    except Exception:
        return []


def main() -> int:
    client = _get_billing_client()
    services = _list_services(client)
    compute_service = None
    for s in services:
        if s.display_name and "Compute Engine" in s.display_name:
            compute_service = s
            break
    if not compute_service:
        print("Compute Engine service not found")
        return 1

    skus = _list_skus(client, compute_service.name)
    eu_prefixes = ("europe-", "eu-")

    # Collect EU CPU and RAM SKUs (on-demand only)
    cpu_skus = []
    ram_skus = []
    for sku in skus:
        if not _is_compute_linux_vm_sku(sku):
            continue
        if _is_preemptible_sku(sku):
            continue
        regions = _service_regions(sku)
        eu_regions = [r for r in regions if any(r.lower().startswith(p) for p in eu_prefixes)]
        if not eu_regions:
            continue
        cat = sku.category
        rg = (cat.resource_group or "").upper() if cat else ""
        if "CPU" in rg:
            cpu_skus.append((sku, eu_regions))
        elif "RAM" in rg:
            ram_skus.append((sku, eu_regions))

    def dump_sku(sku, label: str) -> None:
        print(f"\n{'='*60}")
        print(f"{label}: {getattr(sku, 'name', '')}")
        print(f"  description: {getattr(sku, 'description', '')[:80]}...")
        try:
            pi = sku.pricing_info
            if not pi or len(pi) == 0:
                print("  pricing_info: (empty)")
                return
            expr = pi[0].pricing_expression
            if not expr:
                print("  pricing_expression: (empty)")
                return
            usage_unit = getattr(expr, "usage_unit", None)
            usage_unit_desc = getattr(expr, "usage_unit_description", None)
            base_unit = getattr(expr, "base_unit", None)
            display_qty = getattr(expr, "display_quantity", None)
            print(f"  usage_unit: {repr(usage_unit)}")
            print(f"  usage_unit_description: {repr(usage_unit_desc)}")
            print(f"  base_unit: {repr(base_unit)}")
            print(f"  display_quantity: {repr(display_qty)}")
            tiered = expr.tiered_rates if hasattr(expr, "tiered_rates") else []
            if tiered and len(tiered) > 0:
                up = tiered[0].unit_price
                if up:
                    units = getattr(up, "units", 0) or 0
                    nanos = getattr(up, "nanos", 0) or 0
                    total = int(units) + (int(nanos) / 1e9)
                    print(f"  unit_price: units={units}, nanos={nanos} -> total={total}")
                    print(f"  currency: {getattr(up, 'currency_code', '')}")
        except Exception as e:
            print(f"  error: {e}")

    # Dump first few CPU and RAM SKUs (EU regions)
    print("\n### CPU SKUs (first 3 EU)")
    for sku, regions in cpu_skus[:3]:
        dump_sku(sku, "CPU")
        print(f"  regions (sample): {regions[:3]}")

    print("\n### RAM SKUs (first 3 EU)")
    for sku, regions in ram_skus[:3]:
        dump_sku(sku, "RAM")
        print(f"  regions (sample): {regions[:3]}")

    # Also check europe-west1 specifically
    print("\n### Sample: europe-west1 CPU price calculation")
    for sku, regions in cpu_skus:
        if "europe-west1" in regions:
            dump_sku(sku, "CPU (europe-west1)")
            break

    print("\n### Sample: europe-west1 RAM price calculation")
    for sku, regions in ram_skus:
        if "europe-west1" in regions:
            dump_sku(sku, "RAM (europe-west1)")
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())
