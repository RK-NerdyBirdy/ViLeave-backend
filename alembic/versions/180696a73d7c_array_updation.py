"""Array_updation

Revision ID: 180696a73d7c
Revises: 0001_initial_schema
Create Date: 2026-07-03 22:54:58.617244
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '180696a73d7c'
down_revision = '0001_initial_schema'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Rename the column from 'role' to 'roles'
    op.alter_column('users', 'role', new_column_name='roles')
    
    # 2. Safely cast the existing Enum data into an Array of that Enum
    op.execute(
        "ALTER TABLE users ALTER COLUMN roles TYPE userrole[] USING ARRAY[roles]::userrole[]"
    )


def downgrade() -> None:
    # 1. Revert the Array back to a single Enum. 
    # (Note: This takes the FIRST element of the array [1] since PostgreSQL arrays are 1-indexed)
    op.execute(
        "ALTER TABLE users ALTER COLUMN roles TYPE userrole USING roles[1]::userrole"
    )
    
    # 2. Rename 'roles' back to 'role'
    op.alter_column('users', 'roles', new_column_name='role')