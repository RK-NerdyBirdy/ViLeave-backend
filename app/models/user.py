"""
app/models/user.py
──────────────────
Three joined-table entities sharing a single `users` identity table.

Inheritance strategy: JOINED — each subclass has its own table with a FK
back to `users`. This keeps the identity table lean while allowing
role-specific columns to live in dedicated tables.
"""
import uuid
import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey,
    String, func,
)
# NEW: Import ARRAY from postgresql dialect
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    STUDENT = "STUDENT"
    FACULTY = "FACULTY"
    HOD = "HOD"


class User(Base):
    """
    Central identity table. Every authenticated person has exactly one row.
    Google OAuth email is the unique identifier used for login.
    """
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # UPDATED: 'roles' is now an Array of Enums
    roles: Mapped[list[UserRole]] = mapped_column(ARRAY(Enum(UserRole)), nullable=False)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )

    # Relationships — populated only for the matching role
    student_profile: Mapped["Student | None"] = relationship(
        "Student", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    faculty_profile: Mapped["Faculty | None"] = relationship(
        "Faculty", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} roles={self.roles}>"


class Student(Base):
    """
    Student-specific profile. 1:1 with User (joined table).
    `proctor_id` points to Faculty and is immutable once a leave request
    has been submitted (enforced at the service layer).
    """
    __tablename__ = "students"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True
    )
    registration_no: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    proctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("faculty.id"), nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="student_profile")
    proctor: Mapped["Faculty"] = relationship("Faculty", back_populates="assigned_students")
    leave_requests: Mapped[list["LeaveRequest"]] = relationship(
        "LeaveRequest", back_populates="student",
        foreign_keys="LeaveRequest.student_id"
    )

    def __repr__(self) -> str:
        return f"<Student reg={self.registration_no}>"


class Faculty(Base):
    """
    Faculty / HOD profile. Role distinction (FACULTY vs HOD) lives on User.roles.
    A HOD row exists here too — they can be assigned as a proctor and participate
    in the regular proctor workflow.
    """
    __tablename__ = "faculty"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True
    )
    faculty_id: Mapped[str] = mapped_column(
        String(5), unique=True, nullable=False, index=True
    )  # 5-digit numeric string, e.g. "00123"
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    designation: Mapped[str] = mapped_column(String(100), nullable=False)
    honorific: Mapped[str] = mapped_column(String(20), nullable=False)  # Dr., Prof., etc.

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="faculty_profile")
    assigned_students: Mapped[list["Student"]] = relationship(
        "Student", back_populates="proctor"
    )
    # Requests where this faculty member was the assigned proctor at submission time
    assigned_leave_requests: Mapped[list["LeaveRequest"]] = relationship(
        "LeaveRequest", back_populates="assigned_proctor",
        foreign_keys="LeaveRequest.assigned_proctor_id"
    )

    def __repr__(self) -> str:
        return f"<Faculty faculty_id={self.faculty_id}>"