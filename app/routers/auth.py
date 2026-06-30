"""
app/routers/auth.py  (UPDATED)
───────────────────────────────
GET  /auth/google         — Start Google OAuth sign-in
GET  /auth/google/callback — Exchange Google auth code and redirect with JWT
GET  /auth/me      — Return current user info
POST /auth/logout  — Blocklist the current token (real server-side invalidation)
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

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Secondary bearer extractor — needed to get the raw token string for blocklisting
_bearer = HTTPBearer(auto_error=False)


@router.get("/google", summary="Start Google OAuth login")
async def google_login():
    """
    Starts the Google OAuth authorization-code flow.

    The browser is redirected to Google, then Google sends the user back to
    `/auth/google/callback` with a code. The callback redirects again to the
    configured success URL with the JWT in the URL fragment.
    """
    state = build_google_state()
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
    Returns the authenticated user's identity and role.
    Used by the frontend immediately after login to determine which
    dashboard to render.
    """
    return {
        "id":        str(current_user.id),
        "email":     current_user.email,
        "full_name": current_user.full_name,
        "role":      current_user.role,
        "is_active": current_user.is_active,
    }


@router.post("/logout", summary="Logout — invalidate current token")
async def logout(
    # Validates the token first; raises 401 if already invalid/expired
    current_user: CurrentUser,
    db: DBSession,
    # Also extract the raw token string so we can add its JTI to the blocklist
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
):
    """
    **Real server-side logout.**

    Adds the current token's JTI (unique token ID) to a persistent blocklist.
    Any subsequent request presenting this exact token will receive
    `401 Unauthorized`, even if the token has not yet reached its natural expiry.

    The client should also discard the token locally after calling this endpoint.

    **Blocklist mechanics:**
    - JTI is stored in the `token_blocklist` DB table until the token's `exp`.
    - An in-memory set provides O(1) lookup on every authenticated request
      with no additional DB round-trip.
    - Expired entries are pruned automatically by a background task at startup.
    """
    if credentials:
        await logout_user(token=credentials.credentials, db=db)

    return {"message": "Logged out successfully. Your token has been invalidated."}
