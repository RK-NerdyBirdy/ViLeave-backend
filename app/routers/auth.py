"""
app/routers/auth.py  (UPDATED)
───────────────────────────────
POST /auth/google  — Exchange Google ID token for our JWT
GET  /auth/me      — Return current user info
POST /auth/logout  — Blocklist the current token (real server-side invalidation)
"""
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.dependencies import CurrentUser, DBSession
from app.schemas.auth import GoogleTokenRequest, TokenResponse
from app.services.auth_service import login_with_google, logout_user

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Secondary bearer extractor — needed to get the raw token string for blocklisting
_bearer = HTTPBearer(auto_error=False)


@router.post("/google", response_model=TokenResponse, summary="Login with Google OAuth")
async def google_login(body: GoogleTokenRequest, db: DBSession):
    """
    Accepts a Google ID token from the frontend (obtained after the user
    completes Google Sign-In in the browser).

    Verifies the token with Google's public keys, then checks if the email
    exists and is active in our database.

    Returns our own short-lived JWT. Include it in all subsequent requests:
    `Authorization: Bearer <token>`
    """
    return await login_with_google(body.id_token, db)


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
