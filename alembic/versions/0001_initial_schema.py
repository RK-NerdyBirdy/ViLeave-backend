"""Initial schema — all tables

Revision ID: 0001_initial_schema
Revises:
Create Date: 2024-11-01 00:00:00.000000

This migration creates the complete initial schema:
  - users
  - students
  - faculty
  - leave_requests
  - system_config
  - token_blocklist

Run with:
    alembic upgrade head
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers used by Alembic
revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ─────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column(
            "role",
            sa.Enum("STUDENT", "FACULTY", "HOD", name="userrole"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── faculty ───────────────────────────────────────────────────────────────
    op.create_table(
        "faculty",
        sa.Column("id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("faculty_id", sa.String(5), nullable=False),
        sa.Column("phone_number", sa.String(20), nullable=False),
        sa.Column("designation", sa.String(100), nullable=False),
        sa.Column("honorific", sa.String(20), nullable=False),
    )
    op.create_index("ix_faculty_faculty_id", "faculty", ["faculty_id"], unique=True)

    # ── students ──────────────────────────────────────────────────────────────
    op.create_table(
        "students",
        sa.Column("id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("registration_no", sa.String(20), nullable=False),
        sa.Column("phone_number", sa.String(20), nullable=False),
        sa.Column("proctor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("faculty.id"), nullable=False),
    )
    op.create_index("ix_students_registration_no", "students", ["registration_no"], unique=True)

    # ── system_config ─────────────────────────────────────────────────────────
    op.create_table(
        "system_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("max_leave_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_system_config_single_row"),
    )

    # ── leave_requests ────────────────────────────────────────────────────────
    op.create_table(
        "leave_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("students.id"), nullable=False),
        sa.Column("assigned_proctor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("faculty.id"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING_PROCTOR", "PENDING_HOD",
                "APPROVED", "REJECTED_BY_PROCTOR", "REJECTED_BY_HOD",
                name="leavestatus",
            ),
            nullable=False,
            server_default="PENDING_PROCTOR",
        ),
        sa.Column("pdf_r2_key", sa.String(500), nullable=True),
        sa.Column("proctor_remarks", sa.Text(), nullable=True),
        sa.Column("hod_remarks", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # Composite indexes that mirror the most common query patterns
    op.create_index(
        "ix_leave_requests_student_status",
        "leave_requests", ["student_id", "is_deleted", "status"],
    )
    op.create_index(
        "ix_leave_requests_proctor_status",
        "leave_requests", ["assigned_proctor_id", "status", "is_deleted"],
    )
    op.create_index(
        "ix_leave_requests_status_deleted",
        "leave_requests", ["status", "is_deleted"],
    )

    # ── token_blocklist ───────────────────────────────────────────────────────
    op.create_table(
        "token_blocklist",
        sa.Column("jti", sa.String(64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # Index to speed up the periodic pruning query (WHERE expires_at <= now())
    op.create_index("ix_token_blocklist_expires_at", "token_blocklist", ["expires_at"])


def downgrade() -> None:
    op.drop_table("token_blocklist")
    op.drop_index("ix_leave_requests_status_deleted", "leave_requests")
    op.drop_index("ix_leave_requests_proctor_status", "leave_requests")
    op.drop_index("ix_leave_requests_student_status", "leave_requests")
    op.drop_table("leave_requests")
    op.drop_table("system_config")
    op.drop_index("ix_students_registration_no", "students")
    op.drop_table("students")
    op.drop_index("ix_faculty_faculty_id", "faculty")
    op.drop_table("faculty")
    op.drop_index("ix_users_email", "users")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS leavestatus")
    op.execute("DROP TYPE IF EXISTS userrole")
