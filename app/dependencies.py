"""
app/dependencies.py
────────────────────
FastAPI dependency functions injected into routes via Depends().

Key dependencies:
  - get_current_user   → Any authenticated, active user
  - require_student    → Must be STUDENT role
  - require_faculty    → Must be FACULTY or HOD role (proctors)
  - require_hod        → Must be HOD role only

Usage in a route:
    @router.get("/me")
    async def get_me(current_user: User = Depends(require_student)):
        ...
"""
import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User, UserRole
from app.services.auth_service import decode_access_token, get_user_by_id
from app.utils.exceptions import ForbiddenError, UnauthorizedError

# HTTPBearer extracts the token from the "Authorization: Bearer <token>" header
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """
    Validates the JWT from the Authorization header and returns the User.
    Raises 401 if the token is missing, invalid, or expired.
    Raises 403 if the user account has been deactivated.
    """
    if credentials is None:
        raise UnauthorizedError("No authentication credentials provided")

    payload = decode_access_token(credentials.credentials)
    user_id_str: str | None = payload.get("sub")
    if not user_id_str:
        raise UnauthorizedError("Malformed token: missing subject claim")

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise UnauthorizedError("Malformed token: invalid subject format")

    user = await get_user_by_id(user_id, db)
    if not user:
        raise ForbiddenError("This account has been deactivated or does not exist")

    return user


async def require_student(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.role != UserRole.STUDENT:
        raise ForbiddenError("This endpoint requires Student access")
    return current_user


async def require_faculty(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Both FACULTY and HOD can act as proctors."""
    if current_user.role not in (UserRole.FACULTY, UserRole.HOD):
        raise ForbiddenError("This endpoint requires Faculty access")
    return current_user


async def require_hod(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.role != UserRole.HOD:
        raise ForbiddenError("This endpoint requires HOD access")
    return current_user


# ── Typed shorthand aliases for cleaner route signatures ─────────────────────
CurrentUser = Annotated[User, Depends(get_current_user)]
StudentUser = Annotated[User, Depends(require_student)]
FacultyUser = Annotated[User, Depends(require_faculty)]
HODUser     = Annotated[User, Depends(require_hod)]
DBSession   = Annotated[AsyncSession, Depends(get_db)]
