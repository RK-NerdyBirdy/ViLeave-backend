"""Add student_reason to leave_requests

Revision ID: 9a4d2f8b3c17
Revises: 1c7b5f2a9e41
Create Date: 2026-07-24 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9a4d2f8b3c17"
down_revision = "1c7b5f2a9e41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leave_requests",
        sa.Column(
            "student_reason",
            sa.String(length=1000),
            nullable=False,
            server_default="Legacy request",
        ),
    )
    op.alter_column("leave_requests", "student_reason", server_default=None)


def downgrade() -> None:
    op.drop_column("leave_requests", "student_reason")