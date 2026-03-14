"""Add sustainability columns to regions and providers.

Revision ID: 006
Revises: 005
Create Date: 2026-03-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "regions",
        sa.Column("renewable_energy_pct", sa.Float(), nullable=True),
    )
    op.add_column(
        "regions",
        sa.Column("carbon_intensity_g_co2_kwh", sa.Float(), nullable=True),
    )
    op.add_column(
        "regions",
        sa.Column("publishes_carbon_data", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "providers",
        sa.Column("publishes_carbon_emissions", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("providers", "publishes_carbon_emissions")
    op.drop_column("regions", "publishes_carbon_data")
    op.drop_column("regions", "carbon_intensity_g_co2_kwh")
    op.drop_column("regions", "renewable_energy_pct")
