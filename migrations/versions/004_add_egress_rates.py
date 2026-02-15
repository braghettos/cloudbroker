"""Add egress_rates table for TCO-aware recommendations.

Revision ID: 004
Revises: 003
Create Date: 2026-02-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "egress_rates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_slug", sa.String(length=64), nullable=False),
        sa.Column("egress_eur_per_gb", sa.Float(), nullable=False),
        sa.Column("free_tier_gb_per_month", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cross_region_eur_per_gb", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_slug", name="uq_egress_rates_provider_slug"),
    )
    op.create_index("ix_egress_rates_provider_slug", "egress_rates", ["provider_slug"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_egress_rates_provider_slug", table_name="egress_rates")
    op.drop_table("egress_rates")
