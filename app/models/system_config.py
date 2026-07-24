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
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey,
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
    max_od_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    max_medical_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    special_request_threshold_days: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    cat_2_start_date: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )

    updated_by_user: Mapped["User | None"] = relationship("User")   # type: ignore[name-defined]

    def __repr__(self) -> str:
        return (
            "<SystemConfig "
            f"max_od_days={self.max_od_days} "
            f"max_medical_days={self.max_medical_days} "
            f"special_request_threshold_days={self.special_request_threshold_days} "
            f"cat_2_start_date={self.cat_2_start_date}>"
        )
