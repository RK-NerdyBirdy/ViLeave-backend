"""
app/schemas/leave_request.py
"""
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.leave_request import LeaveStatus


class LeaveRequestCreate(BaseModel):
    """
    Used internally after parsing multipart form data.
    The actual endpoint uses Form() + UploadFile directly.
    """
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_dates(self) -> "LeaveRequestCreate":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.start_date < date.today():
            raise ValueError("start_date cannot be in the past")
        return self


class LeaveRequestUpdate(BaseModel):
    """For PATCH /leave-requests/{id} — all fields optional."""
    start_date: date | None = None
    end_date: date | None = None
    # PDF file is handled separately as UploadFile

    @model_validator(mode="after")
    def validate_dates(self) -> "LeaveRequestUpdate":
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                raise ValueError("end_date must be on or after start_date")
        return self


class ProctorEmbedded(BaseModel):
    id: uuid.UUID
    full_name: str
    honorific: str

    model_config = {"from_attributes": True}


class StudentEmbedded(BaseModel):
    id: uuid.UUID
    full_name: str
    registration_no: str
    email: str

    model_config = {"from_attributes": True}


class LeaveRequestResponse(BaseModel):
    id: uuid.UUID
    student: StudentEmbedded
    assigned_proctor: ProctorEmbedded
    start_date: date
    end_date: date
    duration_days: int
    status: LeaveStatus
    proctor_remarks: str | None
    hod_remarks: str | None
    pdf_url: str | None       # Presigned R2 URL, generated at response time
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PaginatedLeaveRequests(BaseModel):
    total: int
    page: int
    limit: int
    items: list[LeaveRequestResponse]


# ── Decision schemas ──────────────────────────────────────────────────────────

class ProctorDecision(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    remarks: str | None = Field(None, max_length=1000)

    @model_validator(mode="after")
    def remarks_required_on_reject(self) -> "ProctorDecision":
        if self.decision == "REJECT" and not (self.remarks and self.remarks.strip()):
            raise ValueError("remarks are required when rejecting a request")
        return self


class HODDecision(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    remarks: str | None = Field(None, max_length=1000)
