"""
app/dependencies.py
────────────────────
FastAPI dependency functions injected into routes via Depends().

Key dependencies:
  - get_current_user   → Any authenticated, active user
  - require_student    → Must be logged in via STUDENT portal
  - require_faculty    → Must be logged in via FACULTY portal (proctors)
  - require_hod        → Must be logged in via HOD portal

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

    # Extract the specific role they used to log in from the JWT
    token_role_str = payload.get("role")
    if not token_role_str:
        raise UnauthorizedError("Malformed token: missing role claim")
        
    try:
        active_role = UserRole(token_role_str)
    except ValueError:
        raise UnauthorizedError("Malformed token: invalid role claim")

    # Security Check: Ensure they still have this role in the database 
    # (in case an admin revoked their HOD status while they were logged in)
    if active_role not in user.roles:
        raise ForbiddenError("Your permissions have changed. Please log in again.")

    # Attach the active token role to the user object dynamically 
    # so the route-specific dependencies can strictly enforce portal boundaries
    user.active_token_role = active_role

    return user


async def require_student(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.active_token_role != UserRole.STUDENT:
        raise ForbiddenError("This endpoint requires Student access. Please log into the Student portal.")
    return current_user


async def require_faculty(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Strictly requires logging in via the Faculty/Proctor portal."""
    if current_user.active_token_role != UserRole.FACULTY:
        raise ForbiddenError("This endpoint requires Faculty access. Please log into the Faculty portal.")
    return current_user


async def require_hod(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Strictly requires logging in via the HOD portal."""
    if current_user.active_token_role != UserRole.HOD:
        raise ForbiddenError("This endpoint requires HOD access. Please log into the HOD portal.")
    return current_user


# ── Typed shorthand aliases for cleaner route signatures ─────────────────────
CurrentUser = Annotated[User, Depends(get_current_user)]
StudentUser = Annotated[User, Depends(require_student)]
FacultyUser = Annotated[User, Depends(require_faculty)]
HODUser     = Annotated[User, Depends(require_hod)]
DBSession   = Annotated[AsyncSession, Depends(get_db)]