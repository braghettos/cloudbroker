"""Pytest configuration and fixtures for async tests."""

import pytest

from app.core.database import engine


@pytest.fixture(autouse=True, scope="function")
async def cleanup_db_connections():
    """Ensure database connections are properly closed after each test."""
    yield
    # Dispose of all connections in the pool to avoid event loop issues
    await engine.dispose(close=True)
