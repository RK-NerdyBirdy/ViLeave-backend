"""
app/routers/auth.py  (UPDATED)
"""
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.dependencies import CurrentUser, DBSession
from app.services.auth_service import (
    build_google_authorize_url,
    build_google_state,
    complete_google_login,
    logout_user,
)
from app.utils.exceptions import UnauthorizedError
from app.models.user import UserRole

router = APIRouter(prefix="/auth", tags=["Authentication"])

_bearer = HTTPBearer(auto_error=False)


@router.get("/google", summary="Start Google OAuth login")
async def google_login(role: UserRole):
    state = build_google_state(role.value)
    return RedirectResponse(url=build_google_authorize_url(state), status_code=307)


@router.get("/google/callback", summary="Handle Google OAuth callback")
async def google_callback(
    db: DBSession,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if error:
        raise UnauthorizedError(f"Google OAuth failed: {error}")

    if not code or not state:
        raise UnauthorizedError("Missing Google OAuth callback parameters")

    redirect_url = await complete_google_login(code=code, state=state, db=db)
    return RedirectResponse(url=redirect_url, status_code=303)


@router.get("/me", summary="Get current user profile")
async def get_me(current_user: CurrentUser):
    """
    Returns the authenticated user's identity and active session role.
    """
    return {
        "id":          str(current_user.id),
        "email":       current_user.email,
        "full_name":   current_user.full_name,
        "active_role": current_user.active_token_role, # The portal they logged into
        "all_roles":   current_user.roles,             # All DB permissions
        "is_active":   current_user.is_active,
    }


@router.post("/logout", summary="Logout — invalidate current token")
async def logout(
    current_user: CurrentUser,
    db: DBSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
):
    if credentials:
        await logout_user(token=credentials.credentials, db=db)

    return {"message": "Logged out successfully. Your token has been invalidated."}