"""Add composite index on prices for recommendation query performance.

Revision ID: 007
Revises: 006
Create Date: 2026-03-11

"""
from typing import Sequence, Union

from alembic import op


revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_prices_it_region_pm_ts",
        "prices",
        ["instance_type_id", "region_id", "purchase_model", "timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_prices_it_region_pm_ts", table_name="prices")
