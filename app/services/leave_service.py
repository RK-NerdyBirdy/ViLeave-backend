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
from app.models.leave_request import LeavePeriod, LeaveRequest, LeaveStatus, LeaveType, TERMINAL_STATES
from app.models.system_config import SystemConfig
from app.models.user import Faculty, Student, User, UserRole
from app.schemas.leave_request import LeaveRequestResponse, ProctorEmbedded, StudentEmbedded
from app.services.email_queue import EmailQueueManager
from app.services import email_service
from app.services.storage_service import storage, StorageError
from app.utils.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.utils.validators import compute_duration, dates_overlap


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_config_limits(db: AsyncSession) -> tuple[int, int, int, date]:
    result = await db.execute(select(SystemConfig).where(SystemConfig.id == 1))
    config = result.scalar_one_or_none()
    if not config:
        return 7, 7, 5, date.today()

    return (
        config.max_od_days,
        config.max_medical_days,
        config.special_request_threshold_days,
        config.cat_2_start_date,
    )


async def _get_hod_user(db: AsyncSession) -> User:
    result = await db.execute(
        select(User).where(
            User.roles.contains([UserRole.HOD]),
            User.is_active == True  # noqa: E712
        )
    )
    hod = result.scalars().first() 
    
    if not hod:
        raise BadRequestError("No active HOD found in the system")
    return hod


def _classify_leave_period(start_date: date, end_date: date, cat_2_start_date: date) -> LeavePeriod | None:
    if end_date < cat_2_start_date:
        return LeavePeriod.CAT_1
    if start_date >= cat_2_start_date:
        return LeavePeriod.CAT_2
    return None


def _is_special_request(
    duration_days: int,
    max_days: int,
    threshold_days: int,
    leave_period: LeavePeriod | None,
) -> bool:
    return duration_days > max_days or duration_days > threshold_days or leave_period is None



async def _load_request(request_id: uuid.UUID, db: AsyncSession, allow_deleted: bool = False) -> LeaveRequest:
    """Fetch a leave request with all relationships eagerly loaded."""
    query = select(LeaveRequest).where(LeaveRequest.id == request_id)
    
    if not allow_deleted:
        query = query.where(LeaveRequest.is_deleted == False)  # noqa: E712
        
    query = query.options(
        selectinload(LeaveRequest.student).selectinload(Student.user),
        selectinload(LeaveRequest.student).selectinload(Student.proctor).selectinload(Faculty.user),
        selectinload(LeaveRequest.assigned_proctor).selectinload(Faculty.user),
    )
    
    result = await db.execute(query)
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
        student=StudentEmbedded(
            id=req.student.id,
            full_name=student_user.full_name,
            registration_no=req.student.registration_no,
            email=student_user.email,
        ),
        assigned_proctor=ProctorEmbedded(
            id=req.assigned_proctor.id,
            full_name=proctor_user.full_name,
            honorific=req.assigned_proctor.honorific,
        ),
        leave_type=req.leave_type,
        leave_period=req.leave_period,
        student_reason=req.student_reason,
        start_date=req.start_date,
        end_date=req.end_date,
        duration_days=req.duration_days,
        is_special_request=req.is_special_request,
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
    leave_type: LeaveType,
    student_reason: str,
    start_date: date,
    end_date: date,
    pdf_bytes: bytes,
    db: AsyncSession,
    email_queue: EmailQueueManager,
) -> LeaveRequestResponse:
    
    # FIX: Safely and explicitly query the student profile
    result = await db.execute(select(Student).where(Student.id == student_user.id))
    student = result.scalar_one_or_none()
    
    if not student:
        raise ForbiddenError("Student profile not found")

    cleaned_reason = student_reason.strip()
    if not cleaned_reason:
        raise BadRequestError("student_reason cannot be empty")

    duration = compute_duration(start_date, end_date)
    max_od_days, max_medical_days, special_request_threshold_days, cat_2_start_date = await _get_config_limits(db)
    max_days = max_medical_days if leave_type == LeaveType.MEDICAL else max_od_days

    leave_period = _classify_leave_period(start_date, end_date, cat_2_start_date)

    await _check_overlap(student.id, start_date, end_date, db)

    request_id = uuid.uuid4()
    r2_key = storage.upload_pdf(pdf_bytes, student.id, request_id)

    leave_req = LeaveRequest(
        id=request_id,
        student_id=student.id,
        assigned_proctor_id=student.proctor_id,   
        leave_period=leave_period,
        leave_type=leave_type,
        student_reason=cleaned_reason,
        start_date=start_date,
        end_date=end_date,
        duration_days=duration,
        status=LeaveStatus.PENDING_PROCTOR,
        is_special_request=_is_special_request(
            duration,
            max_days,
            special_request_threshold_days,
            leave_period,
        ),
        pdf_r2_key=r2_key,
    )
    db.add(leave_req)
    await db.flush()  

    leave_req = await _load_request(request_id, db)

    pdf_url = storage.generate_presigned_url(r2_key, expires_in=86400)

    if leave_req.is_special_request:
        hod = await _get_hod_user(db)
        await email_queue.enqueue(
            email_service.notify_hod_special_request,
            description="special leave request pre-approval",
            hod_email=hod.email,
            student_name=student_user.full_name,
            student_reg_no=student.registration_no,
            leave_type=leave_type.value,
            student_reason=student_reason.strip(),
            start_date=start_date,
            end_date=end_date,
            duration_days=duration,
            pdf_url=pdf_url,
        )
    else:
        proctor = leave_req.assigned_proctor
        proctor_user = proctor.user
        await email_queue.enqueue(
            email_service.notify_proctor_new_request,
            description="new leave request for proctor review",
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
    req = await _load_request(request_id, db)

    # FIX: Use student_user.id directly
    if req.student_id != student_user.id:
        raise ForbiddenError("You can only edit your own leave requests")

    if req.status != LeaveStatus.PENDING_PROCTOR:
        raise BadRequestError(
            "This request can no longer be edited — it has already been reviewed by your proctor"
        )

    new_start = start_date or req.start_date
    new_end   = end_date   or req.end_date

    if new_end < new_start:
        raise BadRequestError("end_date must be on or after start_date")

    duration = compute_duration(new_start, new_end)
    max_od_days, max_medical_days, special_request_threshold_days, cat_2_start_date = await _get_config_limits(db)
    max_days = max_medical_days if req.leave_type == LeaveType.MEDICAL else max_od_days

    req.leave_period = _classify_leave_period(new_start, new_end, cat_2_start_date)
    req.is_special_request = _is_special_request(
        duration,
        max_days,
        special_request_threshold_days,
        req.leave_period,
    )

    await _check_overlap(student_user.id, new_start, new_end, db, exclude_id=request_id)

    if pdf_bytes:
        old_key = req.pdf_r2_key
        new_key = storage.upload_pdf(pdf_bytes, student_user.id, request_id)
        req.pdf_r2_key = new_key
        if old_key:
            try:
                storage.delete_object(old_key)
            except StorageError:
                pass 

    req.start_date   = new_start
    req.end_date     = new_end
    req.duration_days = duration
    await db.flush()
    req = await _load_request(request_id, db)
    return _build_response(req)


async def delete_leave_request(
    request_id: uuid.UUID,
    student_user: User,
    db: AsyncSession,
) -> None:
    req = await _load_request(request_id, db)

    # FIX: Use student_user.id directly
    if req.student_id != student_user.id:
        raise ForbiddenError("You can only delete your own leave requests")

    if req.status != LeaveStatus.PENDING_PROCTOR:
        raise BadRequestError(
            "This request cannot be deleted — it has already been reviewed by your proctor"
        )

    if req.pdf_r2_key:
        try:
            storage.delete_object(req.pdf_r2_key)
        except StorageError as exc:
            raise BadRequestError(f"Could not remove document from storage: {exc}") from exc

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
    base_query = (
        select(LeaveRequest)
        .where(
            # FIX: Use student_user.id directly
            LeaveRequest.student_id == student_user.id,
            LeaveRequest.is_deleted == False,  # noqa: E712
        )
        .options(
            selectinload(LeaveRequest.student).selectinload(Student.user),
            selectinload(LeaveRequest.assigned_proctor).selectinload(Faculty.user),
        )
        .order_by(LeaveRequest.created_at.desc())
    )
    if status:
        if status == LeaveStatus.PENDING_HOD:
            base_query = base_query.where(
                or_(
                    LeaveRequest.status == LeaveStatus.PENDING_HOD,
                    and_(
                        LeaveRequest.is_special_request == True,  # noqa: E712
                        LeaveRequest.status == LeaveStatus.PENDING_PROCTOR,
                    ),
                )
            )
        else:
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

async def get_leave_request(
    request_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> LeaveRequestResponse:
    req = await _load_request(request_id, db)

    # FIX: Use active_token_role, not roles
    if current_user.active_token_role == UserRole.STUDENT:
        if req.student.user.id != current_user.id:
            raise ForbiddenError("You can only view your own leave requests")

    elif current_user.active_token_role == UserRole.FACULTY:
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
    base_query = (
        select(LeaveRequest)
        .where(
            # FIX: Use faculty_user.id directly
            LeaveRequest.assigned_proctor_id == faculty_user.id,
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
    email_queue: EmailQueueManager,
) -> LeaveRequestResponse:
    req = await _load_request(request_id, db)

    # FIX: Check ownership using faculty_user.id directly
    if req.assigned_proctor_id != faculty_user.id:
        raise ForbiddenError("This request is not assigned to you")

    if req.status != LeaveStatus.PENDING_PROCTOR:
        raise BadRequestError(
            f"This request is in '{req.status}' state and cannot be reviewed as a proctor"
        )

    student_user = req.student.user
    proctor_user = faculty_user
    proctor = req.assigned_proctor # Already eagerly loaded from _load_request!

    if decision == "APPROVE":
        req.status          = LeaveStatus.PENDING_HOD
        req.proctor_remarks = remarks
        hod = await _get_hod_user(db)
        pdf_url = storage.generate_presigned_url(req.pdf_r2_key, expires_in=86400) if req.pdf_r2_key else ""
        
        await email_queue.enqueue(
            email_service.notify_hod_pending_review,
            description="leave request awaiting HOD review",
            hod_email=hod.email,
            student_name=student_user.full_name,
            student_reg_no=req.student.registration_no,
            start_date=req.start_date,
            end_date=req.end_date,
            duration_days=req.duration_days,
            proctor_honorific=proctor.honorific, # FIX: Use eagerly loaded honorific
            proctor_name=proctor_user.full_name,
            proctor_remarks=remarks,
            pdf_url=pdf_url,
        )

    else:  # REJECT
        req.status          = LeaveStatus.REJECTED_BY_PROCTOR
        req.proctor_remarks = remarks

        await email_queue.enqueue(
            email_service.notify_student_rejected_by_proctor,
            description="student rejection by proctor",
            student_email=student_user.email,
            student_name=student_user.full_name,
            start_date=req.start_date,
            end_date=req.end_date,
            proctor_honorific=proctor.honorific, # FIX: Use eagerly loaded honorific
            proctor_name=proctor_user.full_name,
            remarks=remarks or "",
        )

    await db.flush()
    req = await _load_request(request_id, db)
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
        if status == LeaveStatus.PENDING_HOD:
            base_query = base_query.where(
                or_(
                    LeaveRequest.status == LeaveStatus.PENDING_HOD,
                    and_(
                        LeaveRequest.is_special_request == True,  # noqa: E712
                        LeaveRequest.status == LeaveStatus.PENDING_PROCTOR,
                    ),
                )
            )
        else:
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
    assigned_leave_period: LeavePeriod | None,
    db: AsyncSession,
    email_queue: EmailQueueManager,
) -> LeaveRequestResponse:
    req = await _load_request(request_id, db)

    if req.status == LeaveStatus.PENDING_PROCTOR:
        if not req.is_special_request:
            raise BadRequestError(
                "This request has not reached HOD review yet. Wait for proctor approval first."
            )
        if decision == "APPROVE":
            raise BadRequestError(
                "Special requests must be approved by the proctor first. Please wait for proctor approval."
            )
    elif req.status != LeaveStatus.PENDING_HOD:
        raise BadRequestError(
            f"This request is in '{req.status}' state and cannot be reviewed at HOD level"
        )

    student_user = req.student.user
    proctor      = req.assigned_proctor
    proctor_user = proctor.user

    if decision == "APPROVE":
        if req.leave_period is None:
            if assigned_leave_period is None:
                raise BadRequestError(
                    "This request spans the CAT_1/CAT_2 boundary. Provide assigned_leave_period to finalize approval."
                )
            req.leave_period = assigned_leave_period
        elif assigned_leave_period is not None and assigned_leave_period != req.leave_period:
            raise BadRequestError("assigned_leave_period does not match the request's classified leave period")

        req.status      = LeaveStatus.APPROVED
        req.hod_remarks = remarks

        await email_queue.enqueue(
            email_service.notify_student_approved,
            description="student approval by HOD",
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
        await email_queue.enqueue(
            email_service.notify_hod_rejection,
            description="final rejection by HOD",
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
    req = await _load_request(request_id, db, allow_deleted=True)
    return _build_response(req)


async def hod_special_decide(
    request_id: uuid.UUID,
    hod_user: User,
    decision: str,
    remarks: str | None,
    assigned_leave_period: LeavePeriod | None,
    db: AsyncSession,
    email_queue: EmailQueueManager,
) -> LeaveRequestResponse:
    return await hod_decide(
        request_id=request_id,
        hod_user=hod_user,
        decision=decision,
        remarks=remarks,
        assigned_leave_period=assigned_leave_period,
        db=db,
        email_queue=email_queue,
    )
