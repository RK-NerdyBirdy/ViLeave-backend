"""
app/routers/students.py
────────────────────────
Student-facing profile endpoints.
Leave request endpoints live in leave_requests.py.
"""
from fastapi import APIRouter

from app.dependencies import DBSession, StudentUser
from app.models.user import Faculty, Student
from app.schemas.user import StudentProfile
from app.utils.exceptions import NotFoundError
from sqlalchemy.orm import selectinload
from sqlalchemy import select

router = APIRouter(prefix="/students", tags=["Students"])


@router.get("/me", response_model=StudentProfile, summary="Get my student profile")
async def get_my_profile(current_user: StudentUser, db: DBSession):
    """
    Returns the authenticated student's full profile including
    their assigned proctor's details.
    """
    from app.models.user import Student, Faculty

    result = await db.execute(
        select(Student)
        .where(Student.id == current_user.id)
        .options(
            selectinload(Student.user),
            selectinload(Student.proctor).selectinload(Faculty.user),
        )
    )
    student = result.scalar_one_or_none()
    if not student:
        raise NotFoundError("Student profile")

    proctor = student.proctor
    proctor_user = proctor.user

    return StudentProfile(
        id=student.id,
        full_name=current_user.full_name,
        email=current_user.email,
        registration_no=student.registration_no,
        phone_number=student.phone_number,
        is_active=current_user.is_active,
        proctor={
            "id": proctor.id,
            "full_name": proctor_user.full_name,
            "honorific": proctor.honorific,
            "designation": proctor.designation,
            "email": proctor_user.email,
        },
    )
