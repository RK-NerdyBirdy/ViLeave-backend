"""
app/services/leave_service.py
──────────────────────────────
All business logic for the leave request lifecycle.
Routers call these functions — no SQLAlchemy queries live in routers.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.leave_request import LeaveRequest, LeaveStatus, TERMINAL_STATES
from app.models.system_config import SystemConfig
from app.models.user import Faculty, Student, User, UserRole
from app.schemas.leave_request import LeaveRequestResponse
from app.services import email_service
from app.services.storage_service import storage, StorageError
from app.utils.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.utils.validators import compute_duration, dates_overlap


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_max_leave_days(db: AsyncSession) -> int:
    result = await db.execute(select(SystemConfig).where(SystemConfig.id == 1))
    config = result.scalar_one_or_none()
    return config.max_leave_days if config else 7


async def _get_hod_user(db: AsyncSession) -> User:
    result = await db.execute(
        select(User).where(User.role == UserRole.HOD, User.is_active == True)  # noqa: E712
    )
    hod = result.scalar_one_or_none()
    if not hod:
        raise BadRequestError("No active HOD found in the system")
    return hod


async def _load_request(request_id: uuid.UUID, db: AsyncSession) -> LeaveRequest:
    """Fetch a non-deleted leave request with all relationships eagerly loaded."""
    result = await db.execute(
        select(LeaveRequest)
        .where(LeaveRequest.id == request_id, LeaveRequest.is_deleted == False)  # noqa: E712
        .options(
            selectinload(LeaveRequest.student).selectinload(Student.user),
            selectinload(LeaveRequest.student).selectinload(Student.proctor).selectinload(Faculty.user),
            selectinload(LeaveRequest.assigned_proctor).selectinload(Faculty.user),
        )
    )
    req = result.scalar_one_or_none()
    if not req:
        raise NotFoundError("Leave request", str(request_id))
    return req


def _build_response(req: LeaveRequest, db_session: AsyncSession | None = None) -> LeaveRequestResponse:
    """
    Map ORM → Pydantic response.
    Generates a presigned URL only if a PDF key exists.
    """
    pdf_url: str | None = None
    if req.pdf_r2_key:
        try:
            pdf_url = storage.generate_presigned_url(req.pdf_r2_key, expires_in=900)
        except StorageError:
            pdf_url = None   # Degrade gracefully — don't crash the response

    student_user = req.student.user
    proctor_user = req.assigned_proctor.user

    return LeaveRequestResponse(
        id=req.id,
        student={
            "id": req.student.id,
            "full_name": student_user.full_name,
            "registration_no": req.student.registration_no,
            "email": student_user.email,
        },
        assigned_proctor={
            "id": req.assigned_proctor.id,
            "full_name": proctor_user.full_name,
            "honorific": req.assigned_proctor.honorific,
        },
        start_date=req.start_date,
        end_date=req.end_date,
        duration_days=req.duration_days,
        status=req.status,
        proctor_remarks=req.proctor_remarks,
        hod_remarks=req.hod_remarks,
        pdf_url=pdf_url,
        created_at=req.created_at,
        updated_at=req.updated_at,
    )


async def _check_overlap(
    student_id: uuid.UUID,
    start_date: date,
    end_date: date,
    db: AsyncSession,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """
    Prevent a student from having two active requests with overlapping dates.
    An 'active' request is one not yet in a terminal state.
    """
    query = (
        select(LeaveRequest)
        .where(
            LeaveRequest.student_id == student_id,
            LeaveRequest.is_deleted == False,  # noqa: E712
            LeaveRequest.status.not_in(list(TERMINAL_STATES)),
            LeaveRequest.start_date <= end_date,
            LeaveRequest.end_date >= start_date,
        )
    )
    if exclude_id:
        query = query.where(LeaveRequest.id != exclude_id)

    result = await db.execute(query)
    existing = result.scalar_one_or_none()
    if existing:
        raise ConflictError(
            f"You already have an active leave request for an overlapping period "
            f"({existing.start_date} – {existing.end_date})"
        )


# ── Student actions ───────────────────────────────────────────────────────────

async def create_leave_request(
    student_user: User,
    start_date: date,
    end_date: date,
    pdf_bytes: bytes,
    db: AsyncSession,
) -> LeaveRequestResponse:
    """
    Submit a new leave request.
    Steps:
      1. Validate duration against HOD's limit
      2. Check for overlapping active requests
      3. Upload PDF to R2
      4. Persist the DB record
      5. Email the proctor asynchronously (fire-and-forget)
    """
    student = student_user.student_profile
    if not student:
        raise ForbiddenError("Student profile not found")

    # 1. Duration check
    duration = compute_duration(start_date, end_date)
    max_days = await _get_max_leave_days(db)
    if duration > max_days:
        raise BadRequestError(
            f"Requested duration ({duration} days) exceeds the maximum allowed ({max_days} days)"
        )

    # 2. Overlap check
    await _check_overlap(student.id, start_date, end_date, db)

    # 3. Upload PDF
    request_id = uuid.uuid4()
    r2_key = storage.upload_pdf(pdf_bytes, student.id, request_id)

    # 4. Persist
    leave_req = LeaveRequest(
        id=request_id,
        student_id=student.id,
        assigned_proctor_id=student.proctor_id,   # Snapshot at creation
        start_date=start_date,
        end_date=end_date,
        duration_days=duration,
        status=LeaveStatus.PENDING_PROCTOR,
        pdf_r2_key=r2_key,
    )
    db.add(leave_req)
    await db.flush()   # Persist without committing — commit happens in get_db()

    # Reload with relationships for the response
    leave_req = await _load_request(request_id, db)

    # 5. Email proctor (fire-and-forget — failure doesn't abort the request)
    proctor = leave_req.assigned_proctor
    proctor_user = proctor.user
    pdf_url = storage.generate_presigned_url(r2_key, expires_in=86400)   # 24h for email links

    await email_service.notify_proctor_new_request(
        proctor_email=proctor_user.email,
        proctor_honorific=proctor.honorific,
        proctor_name=proctor_user.full_name,
        student_name=student_user.full_name,
        student_reg_no=student.registration_no,
        start_date=start_date,
        end_date=end_date,
        duration_days=duration,
        pdf_url=pdf_url,
    )

    return _build_response(leave_req)


async def update_leave_request(
    request_id: uuid.UUID,
    student_user: User,
    start_date: date | None,
    end_date: date | None,
    pdf_bytes: bytes | None,
    db: AsyncSession,
) -> LeaveRequestResponse:
    """
    Student edits a pending request.
    Only allowed while status == PENDING_PROCTOR.
    """
    req = await _load_request(request_id, db)
    student = student_user.student_profile

    # Ownership check
    if req.student_id != student.id:
        raise ForbiddenError("You can only edit your own leave requests")

    # State guard
    if req.status != LeaveStatus.PENDING_PROCTOR:
        raise BadRequestError(
            "This request can no longer be edited — it has already been reviewed by your proctor"
        )

    new_start = start_date or req.start_date
    new_end   = end_date   or req.end_date

    if new_end < new_start:
        raise BadRequestError("end_date must be on or after start_date")

    # Duration check
    duration = compute_duration(new_start, new_end)
    max_days = await _get_max_leave_days(db)
    if duration > max_days:
        raise BadRequestError(
            f"Updated duration ({duration} days) exceeds the maximum allowed ({max_days} days)"
        )

    # Overlap check (exclude self)
    await _check_overlap(student.id, new_start, new_end, db, exclude_id=request_id)

    # Replace PDF if a new one was uploaded
    if pdf_bytes:
        old_key = req.pdf_r2_key
        new_key = storage.upload_pdf(pdf_bytes, student.id, request_id)
        req.pdf_r2_key = new_key
        # Delete old PDF after successful upload
        if old_key:
            try:
                storage.delete_object(old_key)
            except StorageError:
                pass  # Old file cleanup failure is non-critical

    req.start_date   = new_start
    req.end_date     = new_end
    req.duration_days = duration
    await db.flush()

    return _build_response(req)


async def delete_leave_request(
    request_id: uuid.UUID,
    student_user: User,
    db: AsyncSession,
) -> None:
    """
    Student withdraws a pending request.
    Only allowed while status == PENDING_PROCTOR.
    Hard-deletes the R2 object; soft-deletes the DB record.
    """
    req = await _load_request(request_id, db)
    student = student_user.student_profile

    if req.student_id != student.id:
        raise ForbiddenError("You can only delete your own leave requests")

    if req.status != LeaveStatus.PENDING_PROCTOR:
        raise BadRequestError(
            "This request cannot be deleted — it has already been reviewed by your proctor"
        )

    # Hard delete from R2
    if req.pdf_r2_key:
        try:
            storage.delete_object(req.pdf_r2_key)
        except StorageError as exc:
            raise BadRequestError(f"Could not remove document from storage: {exc}") from exc

    # Soft delete in DB
    req.is_deleted = True
    req.deleted_at = datetime.now(timezone.utc)
    req.pdf_r2_key = None
    await db.flush()


async def get_student_requests(
    student_user: User,
    db: AsyncSession,
    status: LeaveStatus | None = None,
    page: int = 1,
    limit: int = 20,
) -> dict:
    student = student_user.student_profile
    base_query = (
        select(LeaveRequest)
        .where(
            LeaveRequest.student_id == student.id,
            LeaveRequest.is_deleted == False,  # noqa: E712
        )
        .options(
            selectinload(LeaveRequest.student).selectinload(Student.user),
            selectinload(LeaveRequest.assigned_proctor).selectinload(Faculty.user),
        )
        .order_by(LeaveRequest.created_at.desc())
    )
    if status:
        base_query = base_query.where(LeaveRequest.status == status)

    count_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
    total = count_result.scalar_one()

    items_result = await db.execute(
        base_query.offset((page - 1) * limit).limit(limit)
    )
    items = items_result.scalars().all()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "items": [_build_response(r) for r in items],
    }


# ── Single request fetch (role-aware) ─────────────────────────────────────────

async def get_leave_request(
    request_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> LeaveRequestResponse:
    req = await _load_request(request_id, db)

    if current_user.role == UserRole.STUDENT:
        if req.student.user.id != current_user.id:
            raise ForbiddenError("You can only view your own leave requests")

    elif current_user.role == UserRole.FACULTY:
        if req.assigned_proctor.user.id != current_user.id:
            raise ForbiddenError("You can only view requests assigned to you")

    # HOD sees everything — no additional check needed

    return _build_response(req)


# ── Proctor actions ───────────────────────────────────────────────────────────

async def get_proctor_requests(
    faculty_user: User,
    db: AsyncSession,
    status: LeaveStatus | None = LeaveStatus.PENDING_PROCTOR,
    page: int = 1,
    limit: int = 20,
) -> dict:
    faculty = faculty_user.faculty_profile
    base_query = (
        select(LeaveRequest)
        .where(
            LeaveRequest.assigned_proctor_id == faculty.id,
            LeaveRequest.is_deleted == False,  # noqa: E712
        )
        .options(
            selectinload(LeaveRequest.student).selectinload(Student.user),
            selectinload(LeaveRequest.assigned_proctor).selectinload(Faculty.user),
        )
        .order_by(LeaveRequest.created_at.desc())
    )
    if status:
        base_query = base_query.where(LeaveRequest.status == status)

    count_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
    total = count_result.scalar_one()

    items_result = await db.execute(
        base_query.offset((page - 1) * limit).limit(limit)
    )
    items = items_result.scalars().all()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "items": [_build_response(r) for r in items],
    }


async def proctor_decide(
    request_id: uuid.UUID,
    faculty_user: User,
    decision: str,
    remarks: str | None,
    db: AsyncSession,
) -> LeaveRequestResponse:
    req = await _load_request(request_id, db)
    faculty = faculty_user.faculty_profile

    # Ownership: only the assigned proctor can decide
    if req.assigned_proctor_id != faculty.id:
        raise ForbiddenError("This request is not assigned to you")

    if req.status != LeaveStatus.PENDING_PROCTOR:
        raise BadRequestError(
            f"This request is in '{req.status}' state and cannot be reviewed as a proctor"
        )

    student_user = req.student.user
    proctor_user = faculty_user

    if decision == "APPROVE":
        req.status          = LeaveStatus.PENDING_HOD
        req.proctor_remarks = remarks

        # Notify HOD
        hod = await _get_hod_user(db)
        pdf_url = storage.generate_presigned_url(req.pdf_r2_key, expires_in=86400) if req.pdf_r2_key else ""
        await email_service.notify_hod_pending_review(
            hod_email=hod.email,
            student_name=student_user.full_name,
            student_reg_no=req.student.registration_no,
            start_date=req.start_date,
            end_date=req.end_date,
            duration_days=req.duration_days,
            proctor_honorific=faculty_user.faculty_profile.honorific,
            proctor_name=proctor_user.full_name,
            proctor_remarks=remarks,
            pdf_url=pdf_url,
        )

    else:  # REJECT
        req.status          = LeaveStatus.REJECTED_BY_PROCTOR
        req.proctor_remarks = remarks

        await email_service.notify_student_rejected_by_proctor(
            student_email=student_user.email,
            student_name=student_user.full_name,
            start_date=req.start_date,
            end_date=req.end_date,
            proctor_honorific=req.assigned_proctor.honorific,
            proctor_name=proctor_user.full_name,
            remarks=remarks or "",
        )

    await db.flush()
    return _build_response(req)


# ── HOD actions ───────────────────────────────────────────────────────────────

async def get_hod_requests(
    db: AsyncSession,
    status: LeaveStatus | None = LeaveStatus.PENDING_HOD,
    student_name: str | None = None,
    reg_no: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> dict:
    base_query = (
        select(LeaveRequest)
        .where(LeaveRequest.is_deleted == False)  # noqa: E712
        .join(LeaveRequest.student)
        .join(Student.user)
        .options(
            selectinload(LeaveRequest.student).selectinload(Student.user),
            selectinload(LeaveRequest.assigned_proctor).selectinload(Faculty.user),
        )
        .order_by(LeaveRequest.created_at.desc())
    )

    if status:
        base_query = base_query.where(LeaveRequest.status == status)
    if student_name:
        base_query = base_query.where(User.full_name.ilike(f"%{student_name}%"))
    if reg_no:
        base_query = base_query.where(Student.registration_no.ilike(f"%{reg_no}%"))

    count_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
    total = count_result.scalar_one()

    items_result = await db.execute(
        base_query.offset((page - 1) * limit).limit(limit)
    )
    items = items_result.scalars().all()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "items": [_build_response(r) for r in items],
    }


async def hod_decide(
    request_id: uuid.UUID,
    hod_user: User,
    decision: str,
    remarks: str | None,
    db: AsyncSession,
) -> LeaveRequestResponse:
    req = await _load_request(request_id, db)

    if req.status != LeaveStatus.PENDING_HOD:
        raise BadRequestError(
            f"This request is in '{req.status}' state and cannot be reviewed at HOD level"
        )

    student_user = req.student.user
    proctor      = req.assigned_proctor
    proctor_user = proctor.user

    if decision == "APPROVE":
        req.status      = LeaveStatus.APPROVED
        req.hod_remarks = remarks

        await email_service.notify_student_approved(
            student_email=student_user.email,
            student_name=student_user.full_name,
            start_date=req.start_date,
            end_date=req.end_date,
            duration_days=req.duration_days,
        )

    else:  # REJECT
        # 1. Hard-delete the PDF from R2
        if req.pdf_r2_key:
            try:
                storage.delete_object(req.pdf_r2_key)
            except StorageError as exc:
                # Log but don't abort — data retention must still proceed
                print(f"[STORAGE ERROR] Failed to delete {req.pdf_r2_key}: {exc}")

        # 2. Nullify sensitive fields (data retention policy)
        req.status          = LeaveStatus.REJECTED_BY_HOD
        req.pdf_r2_key      = None
        req.proctor_remarks = None
        req.hod_remarks     = None
        req.is_deleted      = True
        req.deleted_at      = datetime.now(timezone.utc)

        # 3. Notify student AND proctor concurrently
        await email_service.notify_hod_rejection(
            student_email=student_user.email,
            student_name=student_user.full_name,
            proctor_email=proctor_user.email,
            proctor_honorific=proctor.honorific,
            proctor_name=proctor_user.full_name,
            student_reg_no=req.student.registration_no,
            start_date=req.start_date,
            end_date=req.end_date,
            duration_days=req.duration_days,
            hod_remarks=remarks,
        )

    await db.flush()
    return _build_response(req)
