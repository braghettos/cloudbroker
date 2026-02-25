"""Request/response logging middleware."""

import time
import uuid
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import get_logger
from app.core.metrics import http_request_duration_seconds, http_requests_total

logger = get_logger(__name__)


def mask_sensitive_headers(headers: dict[str, str]) -> dict[str, str]:
    """Mask sensitive headers (API keys, tokens) in logs."""
    sensitive_keys = {"x-api-key", "authorization", "cookie"}
    masked = {}
    for key, value in headers.items():
        if key.lower() in sensitive_keys:
            masked[key] = "***masked***"
        else:
            masked[key] = value
    return masked


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all API requests and responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate request ID for tracing
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # Start timer
        start_time = time.time()

        # Log request
        logger.info(
            "Request started",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            query_params=dict(request.query_params),
            client_host=request.client.host if request.client else None,
            headers=mask_sensitive_headers(dict(request.headers)),
        )

        try:
            # Process request
            response = await call_next(request)

            # Calculate duration
            duration_seconds = time.time() - start_time
            duration_ms = duration_seconds * 1000

            # Record metrics
            http_requests_total.labels(
                method=request.method,
                endpoint=request.url.path,
                status_code=response.status_code,
            ).inc()
            http_request_duration_seconds.labels(
                method=request.method,
                endpoint=request.url.path,
            ).observe(duration_seconds)

            # Log response
            logger.info(
                "Request completed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )

            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id

            return response

        except Exception:
            # Calculate duration
            duration_seconds = time.time() - start_time
            duration_ms = duration_seconds * 1000

            # Record error metrics (status 500)
            http_requests_total.labels(
                method=request.method,
                endpoint=request.url.path,
                status_code=500,
            ).inc()
            http_request_duration_seconds.labels(
                method=request.method,
                endpoint=request.url.path,
            ).observe(duration_seconds)

            # Log error
            logger.error(
                "Request failed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                duration_ms=round(duration_ms, 2),
                exc_info=True,
            )
            raise
