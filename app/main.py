"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.exc import SQLAlchemyError

from app.api import api_router
from app.api.health import router as health_router
from app.core.config import settings
from app.core.exceptions import CloudBrokerError
from app.core.logging import get_logger
from app.core.middleware import RequestLoggingMiddleware

logger = get_logger(__name__)

# Initialize rate limiter
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.rate_limit_per_minute}/minute"],
)


def _get_request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return 429 when rate limit is exceeded."""
    return JSONResponse(
        status_code=429,
        content={
            "detail": f"Rate limit exceeded: {exc.detail}. Please try again later.",
        },
        headers={"Retry-After": "60"},
    )


def _clean_validation_errors(errors: list[dict]) -> list[dict]:
    """Convert Pydantic v2 error dicts to JSON-serializable form.

    Pydantic v2 stores the original exception object in ctx['error'] for
    constraint violations; we stringify it so JSONResponse can serialize it.
    """
    cleaned = []
    for err in errors:
        new_err = dict(err)
        if "ctx" in new_err and isinstance(new_err["ctx"], dict):
            new_err["ctx"] = {
                k: str(v) if isinstance(v, Exception) else v
                for k, v in new_err["ctx"].items()
            }
        cleaned.append(new_err)
    return cleaned


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return 422 with structured body and request ID."""
    request_id = _get_request_id(request)
    return JSONResponse(
        status_code=422,
        content={
            "detail": _clean_validation_errors(exc.errors()),
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id} if request_id else {},
    )


async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """Return 500 with sanitized message for database errors."""
    request_id = _get_request_id(request)
    logger.error("Database error", request_id=request_id, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "A database error occurred. Please try again later.",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id} if request_id else {},
    )


async def domain_error_handler(request: Request, exc: CloudBrokerError) -> JSONResponse:
    """Return 500 with domain error detail."""
    request_id = _get_request_id(request)
    logger.error("Domain error", request_id=request_id, error=str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "detail": str(exc),
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id} if request_id else {},
    )


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler: return 500 with request ID for debugging."""
    request_id = _get_request_id(request)
    logger.error("Unhandled exception", request_id=request_id, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error.",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id} if request_id else {},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Application startup", app_env=settings.app_env)
    yield
    logger.info("Application shutdown")


app = FastAPI(
    title="CloudBroker",
    description="Multi-cloud compute pricing marketplace. Ingest instance data from 7 providers, normalise prices to EUR, and expose a recommendation engine for the cheapest instances matching constraints.",
    version="0.1.0",
    lifespan=lifespan,
)

# Exception handlers (most specific first)
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(CloudBrokerError, domain_error_handler)
app.add_exception_handler(SQLAlchemyError, sqlalchemy_error_handler)
app.add_exception_handler(Exception, generic_error_handler)

# Add rate limiting
app.state.limiter = limiter

# Add request logging middleware
app.add_middleware(RequestLoggingMiddleware)

# CORS middleware (if origins are configured)
if settings.cors_origins:
    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(health_router)
app.include_router(api_router, prefix="/api")
