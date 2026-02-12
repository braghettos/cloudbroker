"""Health check endpoint."""

from fastapi import APIRouter
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


async def check_database() -> dict[str, str]:
    """Check database connectivity."""
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as e:
        logger.error("Database health check failed", error=str(e))
        return {"status": "error", "error": str(e)}


@router.get("/health", summary="Health check")
async def health() -> dict:
    """
    Health check endpoint with detailed status.

    Returns:
        {
            "status": "ok|degraded|down",
            "checks": {
                "database": {"status": "ok|error", ...}
            }
        }
    """
    checks = {}
    overall_status = "ok"

    # Check database
    db_check = await check_database()
    checks["database"] = db_check
    if db_check["status"] != "ok":
        overall_status = "degraded" if overall_status == "ok" else "down"

    return {
        "status": overall_status,
        "checks": checks,
    }
