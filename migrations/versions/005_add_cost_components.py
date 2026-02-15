"""Add storage_rates, public_ip_rates, os_license_rates, cross_az to egress.

Revision ID: 005
Revises: 004
Create Date: 2026-02-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Storage rates: block storage per GB-month, per provider/region/type
    op.create_table(
        "storage_rates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_slug", sa.String(length=64), nullable=False),
        sa.Column("region_slug", sa.String(length=64), nullable=True),
        sa.Column("storage_type", sa.String(length=32), nullable=False),
        sa.Column("eur_per_gb_month", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_slug",
            "region_slug",
            "storage_type",
            name="uq_storage_rates_provider_region_type",
        ),
    )
    op.create_index(
        "ix_storage_rates_provider_slug",
        "storage_rates",
        ["provider_slug"],
        unique=False,
    )

    # Public IP rates: cost per hour when allocated (often 0 when attached)
    op.create_table(
        "public_ip_rates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_slug", sa.String(length=64), nullable=False),
        sa.Column("eur_per_hour", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_slug", name="uq_public_ip_rates_provider_slug"),
    )
    op.create_index(
        "ix_public_ip_rates_provider_slug",
        "public_ip_rates",
        ["provider_slug"],
        unique=True,
    )

    # OS license rates: surcharge for Windows/RHEL etc per vCPU-hour
    op.create_table(
        "os_license_rates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_slug", sa.String(length=64), nullable=False),
        sa.Column("os_type", sa.String(length=32), nullable=False),
        sa.Column("eur_per_vcpu_hour", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_slug",
            "os_type",
            name="uq_os_license_rates_provider_os",
        ),
    )
    op.create_index(
        "ix_os_license_rates_provider_slug",
        "os_license_rates",
        ["provider_slug"],
        unique=False,
    )

    # Add cross_az_eur_per_gb to egress_rates
    op.add_column(
        "egress_rates",
        sa.Column("cross_az_eur_per_gb", sa.Float(), nullable=True),
    )

    # Add gpu_count to instance_types for GPU filtering
    op.add_column(
        "instance_types",
        sa.Column("gpu_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("instance_types", "gpu_count")
    op.drop_column("egress_rates", "cross_az_eur_per_gb")
    op.drop_index("ix_os_license_rates_provider_slug", table_name="os_license_rates")
    op.drop_table("os_license_rates")
    op.drop_index("ix_public_ip_rates_provider_slug", table_name="public_ip_rates")
    op.drop_table("public_ip_rates")
    op.drop_index("ix_storage_rates_provider_slug", table_name="storage_rates")
    op.drop_table("storage_rates")
