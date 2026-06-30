"""
app/models/system_config.py
────────────────────────────
Single-row configuration table.

The CHECK constraint `id = 1` at the DB level ensures only one row can ever
exist. The service layer uses INSERT ... ON CONFLICT DO UPDATE (upsert)
so the HOD can call PUT /hod/config freely without worrying about
insert-vs-update logic.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey,
    Integer, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SystemConfig(Base):
    __tablename__ = "system_config"
    __table_args__ = (
        # Enforce single row at the database level
        CheckConstraint("id = 1", name="ck_system_config_single_row"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    max_leave_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )

    updated_by_user: Mapped["User | None"] = relationship("User")   # type: ignore[name-defined]

    def __repr__(self) -> str:
        return f"<SystemConfig max_leave_days={self.max_leave_days}>"
