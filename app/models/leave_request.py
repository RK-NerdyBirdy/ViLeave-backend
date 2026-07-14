"""
app/models/leave_request.py
────────────────────────────
LeaveRequest is the central fact table driving the entire workflow.

Key design decisions reflected here:
- `assigned_proctor_id` is snapshotted at creation (immutable).
- `pdf_r2_key` is nullified (not the row) on HOD rejection.
- Soft-delete via `is_deleted` / `deleted_at` instead of hard DELETE.
- `duration_days` is computed and stored at creation to avoid recalculation.
"""
import uuid
import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean, Date, DateTime, Enum, ForeignKey,
    Integer, String, Text, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LeaveStatus(str, enum.Enum):
    PENDING_PROCTOR     = "PENDING_PROCTOR"
    PENDING_HOD         = "PENDING_HOD"
    APPROVED            = "APPROVED"
    REJECTED_BY_PROCTOR = "REJECTED_BY_PROCTOR"
    REJECTED_BY_HOD     = "REJECTED_BY_HOD"


# States from which no further transition is possible
TERMINAL_STATES = {
    LeaveStatus.APPROVED,
    LeaveStatus.REJECTED_BY_PROCTOR,
    LeaveStatus.REJECTED_BY_HOD,
}


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ── Parties ───────────────────────────────────────────────────────────────
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id"), nullable=False, index=True
    )
    # Proctor is snapshotted at creation time — never changes for this request
    assigned_proctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("faculty.id"), nullable=False, index=True
    )

    # ── Dates ─────────────────────────────────────────────────────────────────
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── Workflow ──────────────────────────────────────────────────────────────
    status: Mapped[LeaveStatus] = mapped_column(
        Enum(LeaveStatus),
        nullable=False,
        default=LeaveStatus.PENDING_PROCTOR,
        index=True,
    )

    # ── Document ──────────────────────────────────────────────────────────────
    # R2 object key, e.g. "leaves/2024/student-uuid/request-uuid.pdf"
    # Set to NULL after HOD rejection + R2 deletion
    pdf_r2_key: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── Remarks ───────────────────────────────────────────────────────────────
    # Nullified on HOD rejection per data-retention policy
    proctor_remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    hod_remarks: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Soft delete ───────────────────────────────────────────────────────────
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    student: Mapped["Student"] = relationship(
        "Student", back_populates="leave_requests",
        foreign_keys=[student_id]
    )
    assigned_proctor: Mapped["Faculty"] = relationship(
        "Faculty", back_populates="assigned_leave_requests",
        foreign_keys=[assigned_proctor_id]
    )

    def __repr__(self) -> str:
        return f"<LeaveRequest id={self.id} status={self.status}>"
