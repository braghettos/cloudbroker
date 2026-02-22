#!/usr/bin/env python3
"""
Seed CloudBroker DB so Hetzner ranks first in recommendations.

Used by the disaster-recovery test: lowers Hetzner instance prices so
CloudBroker returns Hetzner as the top recommendation. The controller
then tries Hetzner first, gets 503 from the mock, and falls back to Scaleway.

Run after: make migrate, make ingest-hetzner, make ingest-scaleway
Usage: make seed-dr-hetzner-first
"""

from __future__ import annotations

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

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal


# Multiplier for Hetzner prices: 0.2 makes Hetzner ~5x cheaper than Scaleway,
# ensuring it ranks first when both providers have comparable instances.
HETZNER_PRICE_MULTIPLIER = 0.2


async def seed_dr_hetzner_first() -> int:
    """Lower Hetzner prices so they rank first. Returns number of rows updated."""
    async with AsyncSessionLocal() as session:
        # Update price_eur_per_hour and price_per_hour for Hetzner instance types
        # Join: prices -> instance_types -> providers (slug=hetzner)
        result = await session.execute(
            text("""
            UPDATE prices p
            SET
                price_eur_per_hour = COALESCE(p.price_eur_per_hour, p.price_per_hour) * :mult,
                price_per_hour = p.price_per_hour * :mult
            FROM instance_types it
            JOIN providers pr ON it.provider_id = pr.id
            WHERE p.instance_type_id = it.id
              AND pr.slug = 'hetzner'
              AND p.purchase_model = 'on_demand'
            RETURNING p.id
            """),
            {"mult": HETZNER_PRICE_MULTIPLIER},
        )
        rows = result.fetchall()
        await session.commit()
        return len(rows)


def main() -> int:
    print(f"Seeding DR: lowering Hetzner prices by {1/HETZNER_PRICE_MULTIPLIER:.0f}x so Hetzner ranks first")
    count = asyncio.run(seed_dr_hetzner_first())
    print(f"Updated {count} Hetzner price row(s)")
    return 0 if count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
