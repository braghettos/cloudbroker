"""Base class for SQLAlchemy declarative models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """All models inherit from this base."""

    pass
