"""
app/routers/hod.py
───────────────────
HOD-only endpoints:
  - System config (max leave days)
  - Student CRUD + bulk CSV ingest
  - Faculty CRUD + bulk CSV ingest
  - Leave request export as ZIP (with PDFs and manifest)
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from app.dependencies import DBSession, HODUser
from app.schemas.system_config import SystemConfigResponse, SystemConfigUpdate
from app.schemas.user import (
    BulkIngestResponse,
    FacultyCreate,
    FacultyListItem,
    FacultyProfile,
    FacultyUpdate,
    PaginatedFaculty,
    PaginatedStudents,
    StudentCreate,
    StudentProfile,
    StudentUpdate,
)
from app.services import user_service
from app.services.export_service import export_leave_requests_zip
from app.models.leave_request import LeaveStatus
from app.utils.exceptions import BadRequestError, NotFoundError
from sqlalchemy import select
from app.models.system_config import SystemConfig

router = APIRouter(prefix="/hod", tags=["HOD"])


# ══════════════════════════════════════════════════════════════════════════════
# System Configuration
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/config", response_model=SystemConfigResponse, summary="Get global leave config")
async def get_config(current_user: HODUser, db: DBSession):
    """Returns the current maximum allowed leave days set by the HOD."""
    result = await db.execute(select(SystemConfig).where(SystemConfig.id == 1))
    config = result.scalar_one_or_none()
    if not config:
        # Return default if not yet configured
        return SystemConfigResponse(max_leave_days=7, updated_at=None)
    return config


@router.put("/config", response_model=SystemConfigResponse, summary="Update global leave config")
async def update_config(body: SystemConfigUpdate, current_user: HODUser, db: DBSession):
    """
    Set the maximum number of medical leave days a student may apply for.
    Uses upsert — safe to call even before the row exists.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(SystemConfig)
        .values(id=1, max_leave_days=body.max_leave_days, updated_by=current_user.id)
        .on_conflict_do_update(
            index_elements=["id"],
            set_={"max_leave_days": body.max_leave_days, "updated_by": current_user.id},
        )
    )
    await db.execute(stmt)
    await db.flush()

    result = await db.execute(select(SystemConfig).where(SystemConfig.id == 1))
    return result.scalar_one()


# ══════════════════════════════════════════════════════════════════════════════
# Student Management
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/students", response_model=PaginatedStudents, summary="List all students")
async def list_students(
    current_user: HODUser,
    db: DBSession,
    search: str | None = Query(None, description="Partial match on name or exact reg. no."),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Returns a paginated, searchable list of all students with their proctor assignments."""
    return await user_service.list_students(db=db, search=search, page=page, limit=limit)


@router.post(
    "/students",
    status_code=status.HTTP_201_CREATED,
    summary="Manually add a student",
)
async def add_student(body: StudentCreate, current_user: HODUser, db: DBSession):
    """Create a single student account. Email and registration number must be unique."""
    student = await user_service.create_student(data=body, db=db)
    return {"message": "Student created successfully", "id": str(student.id)}


@router.post(
    "/students/bulk",
    response_model=BulkIngestResponse,
    summary="Bulk ingest students from CSV",
)
async def bulk_students(
    current_user: HODUser,
    db: DBSession,
    csv_file: Annotated[UploadFile, File(description="CSV file with student data")],
):
    """
    Import multiple students from a CSV file.

    **Required columns (exact, case-sensitive):**
    `full_name`, `email`, `registration_no`, `phone_number`, `proctor_faculty_id`

    The file is rejected outright if any required column is missing.
    Each row is processed independently — failures are reported without
    aborting the rest of the import.

    Returns a summary: `{ created, skipped, failed, errors: [{row, email, reason}] }`
    """
    if csv_file.content_type not in ("text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"):
        raise BadRequestError("Only CSV files are accepted")

    csv_bytes = await csv_file.read()
    return await user_service.bulk_ingest_students(csv_bytes=csv_bytes, db=db)


@router.patch("/students/{student_id}", summary="Update a student record")
async def update_student(
    student_id: uuid.UUID,
    body: StudentUpdate,
    current_user: HODUser,
    db: DBSession,
):
    """Update mutable student fields. Email and registration number are immutable."""
    student = await user_service.update_student(student_uuid=student_id, data=body, db=db)
    return {"message": "Student updated successfully", "id": str(student.id)}


@router.delete(
    "/students/{student_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate a student account",
)
async def delete_student(student_id: uuid.UUID, current_user: HODUser, db: DBSession):
    """Soft-deletes the student (sets is_active = False). Leave history is preserved."""
    await user_service.soft_delete_student(student_uuid=student_id, db=db)


# ══════════════════════════════════════════════════════════════════════════════
# Faculty Management
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/faculty", response_model=PaginatedFaculty, summary="List all faculty")
async def list_faculty(
    current_user: HODUser,
    db: DBSession,
    search: str | None = Query(None, description="Partial match on name or faculty ID"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Returns a paginated, searchable list of all faculty members."""
    return await user_service.list_faculty(db=db, search=search, page=page, limit=limit)


@router.post(
    "/faculty",
    status_code=status.HTTP_201_CREATED,
    summary="Manually add a faculty member",
)
async def add_faculty(body: FacultyCreate, current_user: HODUser, db: DBSession):
    """Create a single faculty account. Email and faculty ID must be unique."""
    faculty = await user_service.create_faculty(data=body, db=db)
    return {"message": "Faculty created successfully", "id": str(faculty.id)}


@router.post(
    "/faculty/bulk",
    response_model=BulkIngestResponse,
    summary="Bulk ingest faculty from CSV",
)
async def bulk_faculty(
    current_user: HODUser,
    db: DBSession,
    csv_file: Annotated[UploadFile, File(description="CSV file with faculty data")],
):
    """
    Import multiple faculty members from a CSV file.

    **Required columns (exact, case-sensitive):**
    `full_name`, `email`, `faculty_id`, `phone_number`, `designation`, `honorific`

    Valid honorifics: `Dr.`, `Prof.`, `Mr.`, `Mrs.`, `Ms.`, `Mx.`
    """
    if csv_file.content_type not in ("text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"):
        raise BadRequestError("Only CSV files are accepted")

    csv_bytes = await csv_file.read()
    return await user_service.bulk_ingest_faculty(csv_bytes=csv_bytes, db=db)


@router.patch("/faculty/{faculty_id}", summary="Update a faculty record")
async def update_faculty(
    faculty_id: uuid.UUID,
    body: FacultyUpdate,
    current_user: HODUser,
    db: DBSession,
):
    """Update mutable faculty fields. Email and faculty ID are immutable."""
    faculty = await user_service.update_faculty(faculty_uuid=faculty_id, data=body, db=db)
    return {"message": "Faculty updated successfully", "id": str(faculty.id)}


@router.delete(
    "/faculty/{faculty_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate a faculty account",
)
async def delete_faculty(faculty_id: uuid.UUID, current_user: HODUser, db: DBSession):
    """
    Soft-deletes the faculty member (is_active = False).
    **Blocked** if they have any PENDING_PROCTOR or PENDING_HOD requests assigned.
    """
    await user_service.soft_delete_faculty(faculty_uuid=faculty_id, db=db)


# ══════════════════════════════════════════════════════════════════════════════
# Leave Request Export
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/export/leave-requests",
    summary="Export leave requests with PDFs as a ZIP archive",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"application/zip": {}},
            "description": "ZIP archive containing PDFs and a CSV manifest",
        }
    },
)
async def export_leave_requests(
    current_user: HODUser,
    db: DBSession,
    status_filter: Annotated[LeaveStatus | None, Query(alias="status")] = None,
    date_from: str | None = Query(None, description="Filter start date (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="Filter end date (YYYY-MM-DD)"),
):
    """
    Export filtered leave requests as a downloadable ZIP file.

    **ZIP contents:**
    ```
    medical_leave_export_<timestamp>/
    ├── manifest.csv              ← Index of all requests in this export
    └── pdfs/
        ├── 24CSE0001_John_Doe_2024-11-01_to_2024-11-03.pdf
        └── 24CSE0042_Jane_Smith_2024-11-05_to_2024-11-07.pdf
    ```

    **PDF naming convention:**
    `{registration_no}_{student_name}_{start_date}_to_{end_date}.pdf`

    Requests without a PDF (HOD-rejected, student-withdrawn) are included
    in the manifest but excluded from the `pdfs/` folder.
    """
    from datetime import date as date_type
    parsed_date_from: date_type | None = None
    parsed_date_to: date_type | None = None

    if date_from:
        try:
            from datetime import date as _date
            parsed_date_from = _date.fromisoformat(date_from)
        except ValueError:
            raise BadRequestError("date_from must be in YYYY-MM-DD format")

    if date_to:
        try:
            from datetime import date as _date
            parsed_date_to = _date.fromisoformat(date_to)
        except ValueError:
            raise BadRequestError("date_to must be in YYYY-MM-DD format")

    zip_stream, filename = await export_leave_requests_zip(
        db=db,
        status_filter=status_filter,
        date_from=parsed_date_from,
        date_to=parsed_date_to,
    )

    return StreamingResponse(
        zip_stream,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
