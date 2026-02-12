"""Compute pricing marketplace tables.

Revision ID: 001
Revises:
Create Date: 2025-02-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg


# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enums idempotently (ignore if already exist)
    conn = op.get_bind()
    conn.execute(sa.text(
        "DO $$ BEGIN CREATE TYPE providertype AS ENUM ('hyperscaler', 'eu', 'regional'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    ))
    conn.execute(sa.text(
        "DO $$ BEGIN CREATE TYPE purchasemodel AS ENUM ('on_demand', 'spot', 'reserved'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    ))

    provider_type_column = pg.ENUM(
        "hyperscaler",
        "eu",
        "regional",
        name="providertype",
        create_type=False,
    )

    op.create_table(
        "providers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("website", sa.String(length=512), nullable=True),
        sa.Column("type", provider_type_column, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_providers_name"), "providers", ["name"], unique=False)
    op.create_index(op.f("ix_providers_slug"), "providers", ["slug"], unique=True)

    op.create_table(
        "regions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("is_eu", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "slug", name="uq_region_provider_slug"),
    )
    op.create_index(op.f("ix_regions_provider_id"), "regions", ["provider_id"], unique=False)
    op.create_index(op.f("ix_regions_name"), "regions", ["name"], unique=False)
    op.create_index(op.f("ix_regions_slug"), "regions", ["slug"], unique=False)

    purchase_model_column = pg.ENUM(
        "on_demand",
        "spot",
        "reserved",
        name="purchasemodel",
        create_type=False,
    )

    op.create_table(
        "instance_types",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("family", sa.String(length=64), nullable=True),
        sa.Column("generation", sa.String(length=32), nullable=True),
        sa.Column("vcpu", sa.Integer(), nullable=False),
        sa.Column("ram_gb", sa.Float(), nullable=False),
        sa.Column("arch", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "name", name="uq_instance_type_provider_name"),
    )
    op.create_index(
        op.f("ix_instance_types_provider_id"),
        "instance_types",
        ["provider_id"],
        unique=False,
    )
    op.create_index(op.f("ix_instance_types_name"), "instance_types", ["name"], unique=False)
    op.create_index(op.f("ix_instance_types_family"), "instance_types", ["family"], unique=False)

    op.create_table(
        "prices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instance_type_id", sa.Integer(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("purchase_model", purchase_model_column, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("price_per_hour", sa.Float(), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["instance_type_id"],
            ["instance_types.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["region_id"],
            ["regions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_prices_instance_type_id"),
        "prices",
        ["instance_type_id"],
        unique=False,
    )
    op.create_index(op.f("ix_prices_region_id"), "prices", ["region_id"], unique=False)
    op.create_index(
        op.f("ix_prices_purchase_model"),
        "prices",
        ["purchase_model"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("prices")
    op.drop_table("instance_types")
    op.drop_table("regions")
    op.drop_table("providers")
    sa.Enum(name="purchasemodel").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="providertype").drop(op.get_bind(), checkfirst=True)
