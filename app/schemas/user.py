"""
app/schemas/user.py
────────────────────
Pydantic v2 schemas for Student and Faculty CRUD operations.
Includes format validators for registration_no and faculty_id.
"""
import re
import uuid
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


# ── Validators ────────────────────────────────────────────────────────────────

def validate_registration_no(v: str) -> str:
    """Format: 24XXX0001 — 2-digit year, 3 uppercase letters, 4 digits."""
    if not re.fullmatch(r"\d{2}[A-Z]{3}\d{4}", v):
        raise ValueError(
            "Registration number must match format: 24XXX0001 "
            "(2-digit year + 3 uppercase letters + 4 digits)"
        )
    return v


def validate_faculty_id(v: str) -> str:
    """Exactly 5 numeric digits, leading zeros allowed."""
    if not re.fullmatch(r"\d{5}", v):
        raise ValueError("Faculty ID must be exactly 5 numeric digits (e.g. 00123)")
    return v


# ── Proctor info embedded inside StudentProfile ───────────────────────────────

class ProctorInfo(BaseModel):
    id: uuid.UUID
    full_name: str
    honorific: str
    designation: str
    email: str

    model_config = {"from_attributes": True}


# ── Student schemas ───────────────────────────────────────────────────────────

class StudentCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    registration_no: str
    phone_number: str = Field(..., min_length=7, max_length=20)
    proctor_id: uuid.UUID

    @field_validator("registration_no")
    @classmethod
    def check_registration_no(cls, v: str) -> str:
        return validate_registration_no(v.upper())


class StudentUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=2, max_length=255)
    phone_number: str | None = Field(None, min_length=7, max_length=20)
    proctor_id: uuid.UUID | None = None


class StudentProfile(BaseModel):
    """Full profile returned for /students/me."""
    id: uuid.UUID
    full_name: str
    email: str
    registration_no: str
    phone_number: str
    proctor: ProctorInfo
    is_active: bool

    model_config = {"from_attributes": True}


class StudentListItem(BaseModel):
    """Compact row for HOD list views."""
    id: uuid.UUID
    full_name: str
    email: str
    registration_no: str
    phone_number: str
    proctor_id: uuid.UUID
    proctor_name: str
    is_active: bool

    model_config = {"from_attributes": True}


class PaginatedStudents(BaseModel):
    total: int
    page: int
    limit: int
    items: list[StudentListItem]


# ── Faculty schemas ───────────────────────────────────────────────────────────

VALID_HONORIFICS = {"Dr.", "Prof.", "Mr.", "Mrs.", "Ms.", "Mx."}


class FacultyCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    faculty_id: str
    phone_number: str = Field(..., min_length=7, max_length=20)
    designation: str = Field(..., min_length=2, max_length=100)
    honorific: str

    @field_validator("faculty_id")
    @classmethod
    def check_faculty_id(cls, v: str) -> str:
        return validate_faculty_id(v)

    @field_validator("honorific")
    @classmethod
    def check_honorific(cls, v: str) -> str:
        if v not in VALID_HONORIFICS:
            raise ValueError(f"Honorific must be one of: {', '.join(sorted(VALID_HONORIFICS))}")
        return v


class FacultyUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=2, max_length=255)
    phone_number: str | None = Field(None, min_length=7, max_length=20)
    designation: str | None = Field(None, min_length=2, max_length=100)
    honorific: str | None = None

    @field_validator("honorific")
    @classmethod
    def check_honorific(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_HONORIFICS:
            raise ValueError(f"Honorific must be one of: {', '.join(sorted(VALID_HONORIFICS))}")
        return v


class FacultyProfile(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    faculty_id: str
    phone_number: str
    designation: str
    honorific: str
    is_active: bool

    model_config = {"from_attributes": True}


class FacultyListItem(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    faculty_id: str
    designation: str
    honorific: str
    is_active: bool

    model_config = {"from_attributes": True}


class PaginatedFaculty(BaseModel):
    total: int
    page: int
    limit: int
    items: list[FacultyListItem]


# ── Bulk ingest ───────────────────────────────────────────────────────────────

class BulkIngestError(BaseModel):
    row: int
    email: str | None = None
    reason: str


class BulkIngestResponse(BaseModel):
    created: int
    skipped: int
    failed: int
    errors: list[BulkIngestError]
