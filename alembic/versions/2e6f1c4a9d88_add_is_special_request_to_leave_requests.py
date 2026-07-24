"""Add is_special_request to leave_requests

Revision ID: 2e6f1c4a9d88
Revises: 9a4d2f8b3c17
Create Date: 2026-07-24 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2e6f1c4a9d88"
down_revision = "9a4d2f8b3c17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leave_requests",
        sa.Column(
            "is_special_request",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.alter_column("leave_requests", "is_special_request", server_default=None)


def downgrade() -> None:
    op.drop_column("leave_requests", "is_special_request")