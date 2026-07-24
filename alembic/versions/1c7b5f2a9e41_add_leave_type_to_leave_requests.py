"""Add leave_type to leave_requests

Revision ID: 1c7b5f2a9e41
Revises: d18f4d0d2d31
Create Date: 2026-07-24 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1c7b5f2a9e41"
down_revision = "d18f4d0d2d31"
branch_labels = None
depends_on = None


leave_type_enum = sa.Enum("MEDICAL", "OD", name="leavetype")


def upgrade() -> None:
    leave_type_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "leave_requests",
        sa.Column(
            "leave_type",
            leave_type_enum,
            nullable=False,
            server_default="MEDICAL",
        ),
    )
    op.alter_column("leave_requests", "leave_type", server_default=None)


def downgrade() -> None:
    op.drop_column("leave_requests", "leave_type")
    leave_type_enum.drop(op.get_bind(), checkfirst=True)