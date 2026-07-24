"""Add CAT-2 boundary date and make leave period nullable

Revision ID: 7c2f6a9c8d4e
Revises: 4e3a7f1c9b21
Create Date: 2026-07-24 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7c2f6a9c8d4e"
down_revision = "4e3a7f1c9b21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_config",
        sa.Column(
            "cat_2_start_date",
            sa.Date(),
            nullable=False,
            server_default=sa.text("CURRENT_DATE"),
        ),
    )
    op.alter_column("system_config", "cat_2_start_date", server_default=None)

    op.alter_column("leave_requests", "leave_period", existing_type=sa.Enum(name="leaveperiod"), nullable=True)


def downgrade() -> None:
    op.alter_column("leave_requests", "leave_period", existing_type=sa.Enum(name="leaveperiod"), nullable=False)
    op.drop_column("system_config", "cat_2_start_date")
