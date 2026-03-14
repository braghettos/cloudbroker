"""Enhance instance_type and price schemas with hardware and pricing metadata.

Adds:
- instance_types: gpu_model, gpu_memory_gb, disk_gb, disk_type, network_performance,
  cpu_type, is_current_gen, transfer_tb, is_available
- prices: source_currency, effective_date

Revision ID: 008
Revises: 007
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # InstanceType new columns
    op.add_column("instance_types", sa.Column("gpu_model", sa.String(64), nullable=True))
    op.add_column("instance_types", sa.Column("gpu_memory_gb", sa.Float(), nullable=True))
    op.add_column("instance_types", sa.Column("disk_gb", sa.Float(), nullable=True))
    op.add_column("instance_types", sa.Column("disk_type", sa.String(32), nullable=True))
    op.add_column("instance_types", sa.Column("network_performance", sa.String(64), nullable=True))
    op.add_column("instance_types", sa.Column("cpu_type", sa.String(32), nullable=True))
    op.add_column("instance_types", sa.Column("is_current_gen", sa.Boolean(), nullable=True))
    op.add_column("instance_types", sa.Column("transfer_tb", sa.Float(), nullable=True))
    op.add_column(
        "instance_types",
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    # Price new columns
    op.add_column("prices", sa.Column("source_currency", sa.String(3), nullable=True))
    op.add_column("prices", sa.Column("effective_date", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("prices", "effective_date")
    op.drop_column("prices", "source_currency")

    op.drop_column("instance_types", "is_available")
    op.drop_column("instance_types", "transfer_tb")
    op.drop_column("instance_types", "is_current_gen")
    op.drop_column("instance_types", "cpu_type")
    op.drop_column("instance_types", "network_performance")
    op.drop_column("instance_types", "disk_type")
    op.drop_column("instance_types", "disk_gb")
    op.drop_column("instance_types", "gpu_memory_gb")
    op.drop_column("instance_types", "gpu_model")
