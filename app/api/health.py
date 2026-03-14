"""Health check endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging import get_logger
from app.schemas.health import HealthCheckDetail, HealthResponse

logger = get_logger(__name__)

router = APIRouter(tags=["health"])


async def _check_database(db: AsyncSession) -> HealthCheckDetail:
    """Check database connectivity via the standard session."""
    try:
        await db.execute(text("SELECT 1"))
        return HealthCheckDetail(status="ok")
    except Exception as e:
        logger.error("Database health check failed", error=str(e))
        return HealthCheckDetail(status="error", error=str(e))


@router.get("/health", summary="Health check", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    """Health check endpoint with detailed status."""
    db_check = await _check_database(db)

    overall_status = "ok" if db_check.status == "ok" else "degraded"

    return HealthResponse(
        status=overall_status,
        checks={"database": db_check},
    )
