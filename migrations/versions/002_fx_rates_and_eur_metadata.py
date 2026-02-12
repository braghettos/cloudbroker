"""Add fx_rates, price_eur_per_hour, instance_type metadata_json.

Revision ID: 002
Revises: 001
Create Date: 2025-02-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("from_currency", sa.String(length=3), nullable=False),
        sa.Column("to_currency", sa.String(length=3), nullable=False),
        sa.Column("rate", sa.Float(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_fx_rates_from_to_date",
        "fx_rates",
        ["from_currency", "to_currency", "effective_date"],
        unique=True,
    )

    op.add_column(
        "prices",
        sa.Column("price_eur_per_hour", sa.Float(), nullable=True),
    )

    op.add_column(
        "instance_types",
        sa.Column("metadata_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("instance_types", "metadata_json")
    op.drop_column("prices", "price_eur_per_hour")
    op.drop_table("fx_rates")
