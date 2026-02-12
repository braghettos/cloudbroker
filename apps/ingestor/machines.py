"""CLI wrapper for GCP machine type ingestion."""

import asyncio
import sys
from pathlib import Path

# Project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.core.database import AsyncSessionLocal
from connectors import gcp_machine_types


async def main() -> None:
    async with AsyncSessionLocal() as db:
        await gcp_machine_types.ingest(db, only_eu=True, purchase_model="on_demand")
        await db.commit()


if __name__ == "__main__":
    asyncio.run(main())
