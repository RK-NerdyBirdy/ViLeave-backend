"""Add leave period to leave requests

Revision ID: 4e3a7f1c9b21
Revises: 180696a73d7c
Create Date: 2026-07-24 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4e3a7f1c9b21"
down_revision = "180696a73d7c"
branch_labels = None
depends_on = None


leave_period_enum = sa.Enum("CAT_1", "CAT_2", "FAT", name="leaveperiod")


def upgrade() -> None:
    leave_period_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "leave_requests",
        sa.Column(
            "leave_period",
            leave_period_enum,
            nullable=False,
            server_default="CAT_1",
        ),
    )
    op.alter_column("leave_requests", "leave_period", server_default=None)


def downgrade() -> None:
    op.drop_column("leave_requests", "leave_period")
    leave_period_enum.drop(op.get_bind(), checkfirst=True)
