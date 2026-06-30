"""
app/services/user_service.py
─────────────────────────────
HOD user-management operations: manual add, bulk CSV ingest, list/search, update, soft-delete.
"""
import csv
import io
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import Faculty, Student, User, UserRole
from app.schemas.user import (
    BulkIngestError, BulkIngestResponse,
    FacultyCreate, FacultyUpdate,
    StudentCreate, StudentUpdate,
    StudentListItem, FacultyListItem,
)
from app.utils.exceptions import BadRequestError, ConflictError, NotFoundError, ForbiddenError
from app.utils.validators import is_valid_registration_no, is_valid_faculty_id

# ── CSV column definitions (strict) ──────────────────────────────────────────

STUDENT_CSV_COLUMNS = {"full_name", "email", "registration_no", "phone_number", "proctor_faculty_id"}
FACULTY_CSV_COLUMNS = {"full_name", "email", "faculty_id", "phone_number", "designation", "honorific"}

VALID_HONORIFICS = {"Dr.", "Prof.", "Mr.", "Mrs.", "Ms.", "Mx."}


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _get_faculty_by_faculty_id(faculty_id_str: str, db: AsyncSession) -> Faculty | None:
    result = await db.execute(
        select(Faculty).where(Faculty.faculty_id == faculty_id_str)
    )
    return result.scalar_one_or_none()


async def _get_student_orm(student_uuid: uuid.UUID, db: AsyncSession) -> Student:
    result = await db.execute(
        select(Student)
        .where(Student.id == student_uuid)
        .options(selectinload(Student.user), selectinload(Student.proctor).selectinload(Faculty.user))
    )
    s = result.scalar_one_or_none()
    if not s:
        raise NotFoundError("Student", str(student_uuid))
    return s


async def _get_faculty_orm(faculty_uuid: uuid.UUID, db: AsyncSession) -> Faculty:
    result = await db.execute(
        select(Faculty)
        .where(Faculty.id == faculty_uuid)
        .options(selectinload(Faculty.user))
    )
    f = result.scalar_one_or_none()
    if not f:
        raise NotFoundError("Faculty", str(faculty_uuid))
    return f


# ── Student service ───────────────────────────────────────────────────────────

async def create_student(data: StudentCreate, db: AsyncSession) -> Student:
    """Create a User + Student in one transaction."""
    # Verify proctor exists
    proctor = await _get_faculty_orm(data.proctor_id, db)
    if not proctor.user.is_active:
        raise BadRequestError("The specified proctor is inactive")

    user = User(
        email=data.email,
        full_name=data.full_name,
        role=UserRole.STUDENT,
    )
    db.add(user)
    await db.flush()   # Get the generated UUID

    student = Student(
        id=user.id,
        registration_no=data.registration_no,
        phone_number=data.phone_number,
        proctor_id=data.proctor_id,
    )
    db.add(student)

    try:
        await db.flush()
    except IntegrityError as exc:
        raise ConflictError(
            f"A student with email '{data.email}' or "
            f"registration number '{data.registration_no}' already exists"
        ) from exc

    return student


async def update_student(student_uuid: uuid.UUID, data: StudentUpdate, db: AsyncSession) -> Student:
    student = await _get_student_orm(student_uuid, db)
    user = student.user

    if data.full_name is not None:
        user.full_name = data.full_name
    if data.phone_number is not None:
        student.phone_number = data.phone_number
    if data.proctor_id is not None:
        proctor = await _get_faculty_orm(data.proctor_id, db)
        if not proctor.user.is_active:
            raise BadRequestError("The new proctor is inactive")
        student.proctor_id = data.proctor_id

    await db.flush()
    return student


async def soft_delete_student(student_uuid: uuid.UUID, db: AsyncSession) -> None:
    student = await _get_student_orm(student_uuid, db)
    student.user.is_active = False
    await db.flush()


async def list_students(
    db: AsyncSession,
    search: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> dict:
    base_query = (
        select(Student)
        .join(Student.user)
        .options(selectinload(Student.user), selectinload(Student.proctor).selectinload(Faculty.user))
        .order_by(User.full_name)
    )

    if search:
        base_query = base_query.where(
            or_(
                User.full_name.ilike(f"%{search}%"),
                Student.registration_no.ilike(f"%{search}%"),
            )
        )

    count_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
    total = count_result.scalar_one()

    items_result = await db.execute(base_query.offset((page - 1) * limit).limit(limit))
    students = items_result.scalars().all()

    items = [
        StudentListItem(
            id=s.id,
            full_name=s.user.full_name,
            email=s.user.email,
            registration_no=s.registration_no,
            phone_number=s.phone_number,
            proctor_id=s.proctor_id,
            proctor_name=f"{s.proctor.honorific} {s.proctor.user.full_name}",
            is_active=s.user.is_active,
        )
        for s in students
    ]

    return {"total": total, "page": page, "limit": limit, "items": items}


# ── Faculty service ───────────────────────────────────────────────────────────

async def create_faculty(data: FacultyCreate, db: AsyncSession, role: UserRole = UserRole.FACULTY) -> Faculty:
    user = User(email=data.email, full_name=data.full_name, role=role)
    db.add(user)
    await db.flush()

    faculty = Faculty(
        id=user.id,
        faculty_id=data.faculty_id,
        phone_number=data.phone_number,
        designation=data.designation,
        honorific=data.honorific,
    )
    db.add(faculty)

    try:
        await db.flush()
    except IntegrityError as exc:
        raise ConflictError(
            f"A faculty member with email '{data.email}' or "
            f"faculty ID '{data.faculty_id}' already exists"
        ) from exc

    return faculty


async def update_faculty(faculty_uuid: uuid.UUID, data: FacultyUpdate, db: AsyncSession) -> Faculty:
    faculty = await _get_faculty_orm(faculty_uuid, db)
    user = faculty.user

    if data.full_name is not None:
        user.full_name = data.full_name
    if data.phone_number is not None:
        faculty.phone_number = data.phone_number
    if data.designation is not None:
        faculty.designation = data.designation
    if data.honorific is not None:
        faculty.honorific = data.honorific

    await db.flush()
    return faculty


async def soft_delete_faculty(faculty_uuid: uuid.UUID, db: AsyncSession) -> None:
    """
    Guard: reject if faculty has any pending requests assigned to them.
    HOD must resolve those first.
    """
    from app.models.leave_request import LeaveRequest, LeaveStatus

    faculty = await _get_faculty_orm(faculty_uuid, db)

    # Check for unresolved requests
    pending_result = await db.execute(
        select(func.count(LeaveRequest.id)).where(
            LeaveRequest.assigned_proctor_id == faculty_uuid,
            LeaveRequest.is_deleted == False,  # noqa: E712
            LeaveRequest.status.in_([LeaveStatus.PENDING_PROCTOR, LeaveStatus.PENDING_HOD]),
        )
    )
    pending_count = pending_result.scalar_one()
    if pending_count > 0:
        raise BadRequestError(
            f"Cannot deactivate this faculty member — they have {pending_count} "
            "pending leave request(s) assigned. Resolve them first."
        )

    faculty.user.is_active = False
    await db.flush()


async def list_faculty(
    db: AsyncSession,
    search: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> dict:
    base_query = (
        select(Faculty)
        .join(Faculty.user)
        .options(selectinload(Faculty.user))
        .order_by(User.full_name)
    )

    if search:
        base_query = base_query.where(
            or_(
                User.full_name.ilike(f"%{search}%"),
                Faculty.faculty_id.ilike(f"%{search}%"),
            )
        )

    count_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
    total = count_result.scalar_one()

    items_result = await db.execute(base_query.offset((page - 1) * limit).limit(limit))
    faculty_list = items_result.scalars().all()

    items = [
        FacultyListItem(
            id=f.id,
            full_name=f.user.full_name,
            email=f.user.email,
            faculty_id=f.faculty_id,
            designation=f.designation,
            honorific=f.honorific,
            is_active=f.user.is_active,
        )
        for f in faculty_list
    ]

    return {"total": total, "page": page, "limit": limit, "items": items}


# ── Bulk CSV ingest ───────────────────────────────────────────────────────────

def _parse_csv(raw_bytes: bytes, required_columns: set[str]) -> tuple[list[dict], str | None]:
    """
    Parse CSV bytes.
    Returns (rows, error_message).
    error_message is set only if the header is invalid.
    """
    try:
        text = raw_bytes.decode("utf-8-sig")   # Handle BOM from Excel exports
        reader = csv.DictReader(io.StringIO(text))
        actual_cols = set(reader.fieldnames or [])
        missing = required_columns - actual_cols
        extra   = actual_cols - required_columns

        if missing:
            return [], (
                f"Missing required columns: {', '.join(sorted(missing))}. "
                f"Required: {', '.join(sorted(required_columns))}"
            )

        return list(reader), None
    except Exception as exc:
        return [], f"Failed to parse CSV: {exc}"


async def bulk_ingest_students(csv_bytes: bytes, db: AsyncSession) -> BulkIngestResponse:
    rows, error = _parse_csv(csv_bytes, STUDENT_CSV_COLUMNS)
    if error:
        raise BadRequestError(error)

    created = skipped = failed = 0
    errors: list[BulkIngestError] = []

    for idx, row in enumerate(rows, start=2):   # Row 1 is the header
        email         = row.get("email", "").strip()
        full_name     = row.get("full_name", "").strip()
        registration_no = row.get("registration_no", "").strip().upper()
        phone_number  = row.get("phone_number", "").strip()
        proctor_fid   = row.get("proctor_faculty_id", "").strip()

        # Row-level validation
        row_errors = []
        if not email:
            row_errors.append("email is required")
        if not is_valid_registration_no(registration_no):
            row_errors.append(f"invalid registration_no format: '{registration_no}'")
        if not is_valid_faculty_id(proctor_fid):
            row_errors.append(f"invalid proctor_faculty_id format: '{proctor_fid}'")
        if not full_name:
            row_errors.append("full_name is required")

        if row_errors:
            failed += 1
            errors.append(BulkIngestError(row=idx, email=email or None, reason="; ".join(row_errors)))
            continue

        # Resolve proctor
        proctor = await _get_faculty_by_faculty_id(proctor_fid, db)
        if not proctor:
            failed += 1
            errors.append(BulkIngestError(row=idx, email=email, reason=f"No faculty found with faculty_id '{proctor_fid}'"))
            continue

        # Check for existing email/reg_no
        existing = await db.execute(
            select(User).where(User.email == email)
        )
        if existing.scalar_one_or_none():
            skipped += 1
            errors.append(BulkIngestError(row=idx, email=email, reason="Email already exists — skipped"))
            continue

        try:
            user = User(email=email, full_name=full_name, role=UserRole.STUDENT)
            db.add(user)
            await db.flush()

            student = Student(
                id=user.id,
                registration_no=registration_no,
                phone_number=phone_number,
                proctor_id=proctor.id,
            )
            db.add(student)
            await db.flush()
            created += 1

        except IntegrityError:
            await db.rollback()
            failed += 1
            errors.append(BulkIngestError(row=idx, email=email, reason="Duplicate registration_no or email"))

    return BulkIngestResponse(created=created, skipped=skipped, failed=failed, errors=errors)


async def bulk_ingest_faculty(csv_bytes: bytes, db: AsyncSession) -> BulkIngestResponse:
    rows, error = _parse_csv(csv_bytes, FACULTY_CSV_COLUMNS)
    if error:
        raise BadRequestError(error)

    created = skipped = failed = 0
    errors: list[BulkIngestError] = []

    for idx, row in enumerate(rows, start=2):
        email       = row.get("email", "").strip()
        full_name   = row.get("full_name", "").strip()
        faculty_id  = row.get("faculty_id", "").strip()
        phone       = row.get("phone_number", "").strip()
        designation = row.get("designation", "").strip()
        honorific   = row.get("honorific", "").strip()

        row_errors = []
        if not email:
            row_errors.append("email is required")
        if not is_valid_faculty_id(faculty_id):
            row_errors.append(f"invalid faculty_id format: '{faculty_id}'")
        if honorific not in VALID_HONORIFICS:
            row_errors.append(f"invalid honorific '{honorific}', must be one of {VALID_HONORIFICS}")
        if not full_name:
            row_errors.append("full_name is required")

        if row_errors:
            failed += 1
            errors.append(BulkIngestError(row=idx, email=email or None, reason="; ".join(row_errors)))
            continue

        existing = await db.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none():
            skipped += 1
            errors.append(BulkIngestError(row=idx, email=email, reason="Email already exists — skipped"))
            continue

        try:
            user = User(email=email, full_name=full_name, role=UserRole.FACULTY)
            db.add(user)
            await db.flush()

            f = Faculty(
                id=user.id,
                faculty_id=faculty_id,
                phone_number=phone,
                designation=designation,
                honorific=honorific,
            )
            db.add(f)
            await db.flush()
            created += 1

        except IntegrityError:
            await db.rollback()
            failed += 1
            errors.append(BulkIngestError(row=idx, email=email, reason="Duplicate faculty_id or email"))

    return BulkIngestResponse(created=created, skipped=skipped, failed=failed, errors=errors)
