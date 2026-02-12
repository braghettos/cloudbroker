"""Security utilities: API key authentication."""

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.core.config import settings

# API key header name
API_KEY_HEADER_NAME = "X-API-Key"

# Create API key header dependency
api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)


async def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """
    Verify API key from header.

    Args:
        api_key: API key from X-API-Key header

    Returns:
        The API key if valid

    Raises:
        HTTPException: If API key is missing or invalid
    """
    # In development, allow requests without API key if API_KEY is not set
    if settings.app_env == "development" and not settings.api_key:
        return "dev-bypass"

    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing API key. Provide X-API-Key header.",
        )

    if api_key != settings.api_key:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key.",
        )

    return api_key
