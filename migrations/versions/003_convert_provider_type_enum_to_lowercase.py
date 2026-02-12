"""Convert providertype enum from uppercase to lowercase values.

Revision ID: 003
Revises: 002
Create Date: 2025-02-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    
    # Check current enum values
    result = conn.execute(text(
        "SELECT unnest(enum_range(NULL::providertype))::text AS value ORDER BY value"
    ))
    current_values = [row[0] for row in result]
    
    # If enum already has lowercase values, skip conversion
    if current_values == ['eu', 'hyperscaler', 'regional']:
        return
    
    # Step 1: Rename old enum to temporary name
    conn.execute(text("ALTER TYPE providertype RENAME TO providertype_old"))
    
    # Step 2: Create new enum with lowercase values
    conn.execute(text(
        "CREATE TYPE providertype AS ENUM ('hyperscaler', 'eu', 'regional')"
    ))
    
    # Step 3: Alter column to use new enum, converting values
    conn.execute(text("""
        ALTER TABLE providers 
        ALTER COLUMN type TYPE providertype 
        USING CASE 
            WHEN type::text = 'HYPERSCALER' THEN 'hyperscaler'::providertype
            WHEN type::text = 'EU' THEN 'eu'::providertype
            WHEN type::text = 'REGIONAL' THEN 'regional'::providertype
            WHEN type::text = 'hyperscaler' THEN 'hyperscaler'::providertype
            WHEN type::text = 'eu' THEN 'eu'::providertype
            WHEN type::text = 'regional' THEN 'regional'::providertype
            ELSE 'regional'::providertype
        END
    """))
    
    # Step 4: Drop old enum
    conn.execute(text("DROP TYPE providertype_old"))


def downgrade() -> None:
    conn = op.get_bind()
    
    # Check current enum values
    result = conn.execute(text(
        "SELECT unnest(enum_range(NULL::providertype))::text AS value ORDER BY value"
    ))
    current_values = [row[0] for row in result]
    
    # If enum already has uppercase values, skip conversion
    if current_values == ['EU', 'HYPERSCALER', 'REGIONAL']:
        return
    
    # Step 1: Rename current enum to temporary name
    conn.execute(text("ALTER TYPE providertype RENAME TO providertype_old"))
    
    # Step 2: Create new enum with uppercase values
    conn.execute(text(
        "CREATE TYPE providertype AS ENUM ('HYPERSCALER', 'EU', 'REGIONAL')"
    ))
    
    # Step 3: Alter column to use new enum, converting values
    conn.execute(text("""
        ALTER TABLE providers 
        ALTER COLUMN type TYPE providertype 
        USING CASE 
            WHEN type::text = 'hyperscaler' THEN 'HYPERSCALER'::providertype
            WHEN type::text = 'eu' THEN 'EU'::providertype
            WHEN type::text = 'regional' THEN 'REGIONAL'::providertype
            WHEN type::text = 'HYPERSCALER' THEN 'HYPERSCALER'::providertype
            WHEN type::text = 'EU' THEN 'EU'::providertype
            WHEN type::text = 'REGIONAL' THEN 'REGIONAL'::providertype
            ELSE 'REGIONAL'::providertype
        END
    """))
    
    # Step 4: Drop old enum
    conn.execute(text("DROP TYPE providertype_old"))
