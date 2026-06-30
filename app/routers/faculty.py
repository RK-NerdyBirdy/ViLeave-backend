"""
app/routers/faculty.py
───────────────────────
Faculty-facing profile endpoints.
Leave request / proctor decision endpoints live in leave_requests.py.
"""
from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.dependencies import DBSession, FacultyUser
from app.models.user import Faculty
from app.schemas.user import FacultyProfile
from app.utils.exceptions import NotFoundError

router = APIRouter(prefix="/faculty", tags=["Faculty"])


@router.get("/me", response_model=FacultyProfile, summary="Get my faculty profile")
async def get_my_profile(current_user: FacultyUser, db: DBSession):
    """Returns the authenticated faculty member's profile."""
    result = await db.execute(
        select(Faculty)
        .where(Faculty.id == current_user.id)
        .options(selectinload(Faculty.user))
    )
    faculty = result.scalar_one_or_none()
    if not faculty:
        raise NotFoundError("Faculty profile")

    return FacultyProfile(
        id=faculty.id,
        full_name=current_user.full_name,
        email=current_user.email,
        faculty_id=faculty.faculty_id,
        phone_number=faculty.phone_number,
        designation=faculty.designation,
        honorific=faculty.honorific,
        is_active=current_user.is_active,
    )
