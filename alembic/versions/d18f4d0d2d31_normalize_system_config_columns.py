"""Normalize system_config columns

Revision ID: d18f4d0d2d31
Revises: 7c2f6a9c8d4e
Create Date: 2026-07-24 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d18f4d0d2d31"
down_revision = "7c2f6a9c8d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("system_config", "max_leave_days", new_column_name="max_od_days")

    op.add_column(
        "system_config",
        sa.Column("max_medical_days", sa.Integer(), nullable=False, server_default="7"),
    )
    op.add_column(
        "system_config",
        sa.Column("special_request_threshold_days", sa.Integer(), nullable=False, server_default="5"),
    )

    op.alter_column("system_config", "max_medical_days", server_default=None)
    op.alter_column("system_config", "special_request_threshold_days", server_default=None)


def downgrade() -> None:
    op.drop_column("system_config", "special_request_threshold_days")
    op.drop_column("system_config", "max_medical_days")
    op.alter_column("system_config", "max_od_days", new_column_name="max_leave_days")