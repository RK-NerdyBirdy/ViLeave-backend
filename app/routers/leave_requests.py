"""
app/routers/leave_requests.py
──────────────────────────────
All leave request endpoints:
  Student:   POST (submit), PATCH (edit), DELETE (withdraw), GET (own list)
  Proctor:   GET (inbox), PATCH /proctor-decision
  HOD:       GET (all), PATCH /hod-decision
  Shared:    GET /{id} (role-aware)
"""
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, UploadFile, status

from app.dependencies import CurrentUser, DBSession, FacultyUser, HODUser, StudentUser
from app.models.leave_request import LeaveStatus
from app.schemas.leave_request import (
    HODDecision,
    LeaveRequestResponse,
    PaginatedLeaveRequests,
    ProctorDecision,
)
from app.services import leave_service
from app.utils.exceptions import BadRequestError, UnprocessableError
from app.config import get_settings

settings = get_settings()

router = APIRouter(prefix="/leave-requests", tags=["Leave Requests"])

# ── PDF validation helper ─────────────────────────────────────────────────────

async def _validate_pdf(pdf_file: UploadFile) -> bytes:
    """Validates MIME type, extension, and size of an uploaded PDF."""
    if pdf_file.content_type not in ("application/pdf",):
        raise BadRequestError("Only PDF files are accepted")
    if not (pdf_file.filename or "").lower().endswith(".pdf"):
        raise BadRequestError("File must have a .pdf extension")

    content = await pdf_file.read()
    if len(content) > settings.pdf_max_size_bytes:
        raise BadRequestError(
            f"PDF exceeds maximum allowed size of {settings.pdf_max_size_mb}MB"
        )
    if len(content) < 100:
        raise BadRequestError("Uploaded file appears to be empty or corrupt")

    # Basic PDF magic bytes check (%PDF-)
    if not content.startswith(b"%PDF-"):
        raise BadRequestError("Uploaded file is not a valid PDF")

    return content

# ── Student: Submit ───────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=LeaveRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a new leave request",
)
async def submit_leave_request(
    current_user: StudentUser,
    db: DBSession,
    start_date: Annotated[date, Form(description="Leave start date (YYYY-MM-DD)")],
    end_date: Annotated[date, Form(description="Leave end date (YYYY-MM-DD)")],
    pdf_file: Annotated[UploadFile, File(description="Medical certificate PDF (max 10MB)")],
):
    """
    Submit a new medical leave request.

    **Multipart form data:**
    - `start_date`: YYYY-MM-DD (Can be in the past)
    - `end_date`: YYYY-MM-DD
    - `pdf_file`: Medical certificate (PDF, max 10MB)

    Validates:
    - Duration ≤ HOD's global max_leave_days (Checked in service layer)
    - No overlapping active request for the same period

    On success: uploads PDF to R2, creates DB record, emails the assigned proctor.
    """
    # 1. Basic sanity check: end date cannot be before start date
    if end_date < start_date:
        raise BadRequestError("end_date must be on or after start_date")

    # 2. Validate the PDF
    pdf_bytes = await _validate_pdf(pdf_file)

    # 3. Pass to service layer (Duration check happens here)
    return await leave_service.create_leave_request(
        student_user=current_user,
        start_date=start_date,
        end_date=end_date,
        pdf_bytes=pdf_bytes,
        db=db,
    )


# ── Student: Edit ─────────────────────────────────────────────────────────────

@router.patch(
    "/{request_id}",
    response_model=LeaveRequestResponse,
    summary="Edit a pending leave request (student only)",
)
async def edit_leave_request(
    request_id: uuid.UUID,
    current_user: StudentUser,
    db: DBSession,
    start_date: Annotated[date | None, Form()] = None,
    end_date: Annotated[date | None, Form()] = None,
    pdf_file: Annotated[UploadFile | None, File()] = None,
):
    """
    Edit an existing leave request.

    **Only allowed while `status == PENDING_PROCTOR`.**
    All fields are optional — send only what you want to change.
    If a new PDF is provided, the old one is deleted from R2.
    """
    pdf_bytes: bytes | None = None
    if pdf_file and pdf_file.filename:
        pdf_bytes = await _validate_pdf(pdf_file)

    return await leave_service.update_leave_request(
        request_id=request_id,
        student_user=current_user,
        start_date=start_date,
        end_date=end_date,
        pdf_bytes=pdf_bytes,
        db=db,
    )


# ── Student: Withdraw ─────────────────────────────────────────────────────────

@router.delete(
    "/{request_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Withdraw a pending leave request (student only)",
)
async def delete_leave_request(
    request_id: uuid.UUID,
    current_user: StudentUser,
    db: DBSession,
):
    """
    Withdraw (soft-delete) a leave request.

    **Only allowed while `status == PENDING_PROCTOR`.**
    The PDF is permanently removed from R2.
    The DB record is soft-deleted (audit trail preserved).
    """
    await leave_service.delete_leave_request(
        request_id=request_id,
        student_user=current_user,
        db=db,
    )


# ── Student: Own history ──────────────────────────────────────────────────────

@router.get(
    "/my",
    response_model=PaginatedLeaveRequests,
    summary="List my leave requests (student only)",
)
async def list_my_requests(
    current_user: StudentUser,
    db: DBSession,
    status_filter: Annotated[LeaveStatus | None, Query(alias="status")] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Returns the authenticated student's leave request history, newest first."""
    result = await leave_service.get_student_requests(
        student_user=current_user,
        db=db,
        status=status_filter,
        page=page,
        limit=limit,
    )
    return result


# ── Shared: Single request detail ─────────────────────────────────────────────

@router.get(
    "/{request_id}",
    response_model=LeaveRequestResponse,
    summary="Get a single leave request (role-aware access)",
)
async def get_leave_request(
    request_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
):
    """
    Fetch a leave request by ID.

    **Access control:**
    - Student: can only view their own requests
    - Faculty (Proctor): can only view requests assigned to them
    - HOD: can view all requests

    The response includes a short-lived presigned PDF URL (15-minute TTL).
    """
    return await leave_service.get_leave_request(
        request_id=request_id,
        current_user=current_user,
        db=db,
    )


# ── Proctor: Inbox ────────────────────────────────────────────────────────────

@router.get(
    "/proctor/inbox",
    response_model=PaginatedLeaveRequests,
    summary="List leave requests assigned to me (proctor only)",
)
async def proctor_inbox(
    current_user: FacultyUser,
    db: DBSession,
    status_filter: Annotated[LeaveStatus | None, Query(alias="status")] = LeaveStatus.PENDING_PROCTOR,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Returns all leave requests assigned to the logged-in proctor.
    Defaults to `PENDING_PROCTOR` status — pass `?status=` to see historical requests.
    """
    result = await leave_service.get_proctor_requests(
        faculty_user=current_user,
        db=db,
        status=status_filter,
        page=page,
        limit=limit,
    )
    return result


# ── Proctor: Decision ─────────────────────────────────────────────────────────

@router.patch(
    "/{request_id}/proctor-decision",
    response_model=LeaveRequestResponse,
    summary="Approve or reject a leave request (proctor only)",
)
async def proctor_decision(
    request_id: uuid.UUID,
    body: ProctorDecision,
    current_user: FacultyUser,
    db: DBSession,
):
    """
    Submit the proctor's decision on a leave request.

    **`decision: "APPROVE"`** → Status moves to `PENDING_HOD`. HOD is emailed.
    **`decision: "REJECT"`** → Status moves to `REJECTED_BY_PROCTOR`. Student is emailed.
    `remarks` is **required** when rejecting.
    """
    return await leave_service.proctor_decide(
        request_id=request_id,
        faculty_user=current_user,
        decision=body.decision,
        remarks=body.remarks,
        db=db,
    )


# ── HOD: All requests ─────────────────────────────────────────────────────────

@router.get(
    "/hod/queue",
    response_model=PaginatedLeaveRequests,
    summary="List all leave requests (HOD only)",
)
async def hod_queue(
    current_user: HODUser,
    db: DBSession,
    status_filter: Annotated[LeaveStatus | None, Query(alias="status")] = LeaveStatus.PENDING_HOD,
    student_name: str | None = Query(None, description="Filter by student name (partial match)"),
    reg_no: str | None = Query(None, description="Filter by registration number"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Returns all leave requests across the institution.
    Supports filtering by status, student name, and registration number.
    Defaults to `PENDING_HOD` status.
    """
    result = await leave_service.get_hod_requests(
        db=db,
        status=status_filter,
        student_name=student_name,
        reg_no=reg_no,
        page=page,
        limit=limit,
    )
    return result


# ── HOD: Final Decision ───────────────────────────────────────────────────────

@router.patch(
    "/{request_id}/hod-decision",
    response_model=LeaveRequestResponse,
    summary="Final approval or rejection (HOD only)",
)
async def hod_decision(
    request_id: uuid.UUID,
    body: HODDecision,
    current_user: HODUser,
    db: DBSession,
):
    """
    Submit the HOD's final decision.

    **`decision: "APPROVE"`**
    - Status → `APPROVED`
    - PDF retained in R2
    - Student receives approval email

    **`decision: "REJECT"`**
    - PDF hard-deleted from R2
    - Sensitive fields nullified (proctor_remarks, hod_remarks, pdf_r2_key)
    - Record soft-deleted (is_deleted = true)
    - Student AND proctor receive rejection emails
    """
    return await leave_service.hod_decide(
        request_id=request_id,
        hod_user=current_user,
        decision=body.decision,
        remarks=body.remarks,
        db=db,
    )
