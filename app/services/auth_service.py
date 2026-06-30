"""
app/services/auth_service.py  (UPDATED)
──────────────────────────────────────────
Changes vs original:
  - create_access_token now embeds a `jti` (JWT ID) claim — a unique ID
    per token that the blocklist uses for revocation.
  - decode_access_token checks the blocklist after signature validation.
  - logout_user adds the token's JTI to the blocklist.
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models.user import User, UserRole
from app.services.token_blocklist import block_token, is_token_blocked
from app.utils.exceptions import ForbiddenError, UnauthorizedError

settings = get_settings()


# ── Google token verification ─────────────────────────────────────────────────

async def verify_google_token(token: str) -> dict:
    """Verify a Google ID token and return its claims. Raises 401 on failure."""
    try:
        request_adapter = google_requests.Request()
        claims = google_id_token.verify_oauth2_token(
            token,
            request_adapter,
            settings.google_client_id,
        )
        if not claims.get("email_verified"):
            raise UnauthorizedError("Google account email is not verified")
        return claims
    except ValueError as exc:
        raise UnauthorizedError(f"Invalid Google token: {exc}") from exc
    except google_exceptions.TransportError as exc:
        # Network failure while fetching Google's public certs — not the
        # client's fault, but we still can't verify the token. Surface as
        # a 503 rather than letting it bubble up as an unhandled 500.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not reach Google's authentication servers. Please try again shortly.",
        ) from exc


# ── JWT creation ──────────────────────────────────────────────────────────────

def create_access_token(user_id: uuid.UUID, role: UserRole, email: str) -> str:
    """
    Create a signed JWT with a unique `jti` claim.
    The `jti` enables per-token revocation via the blocklist.
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub":   str(user_id),
        "email": email,
        "role":  role.value,
        "exp":   expire,
        "iat":   now,
        "jti":   str(uuid.uuid4()),   # unique per-token ID for revocation
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


# ── JWT validation ────────────────────────────────────────────────────────────

def decode_access_token(token: str) -> dict:
    """
    Decode and validate our JWT.
    Checks:
      1. Signature validity
      2. Expiry (exp claim)
      3. Blocklist (via in-memory O(1) set)
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise UnauthorizedError(f"Token validation failed: {exc}") from exc

    jti = payload.get("jti")
    if jti and is_token_blocked(jti):
        raise UnauthorizedError("This token has been revoked. Please log in again.")

    return payload


# ── Database user lookup ──────────────────────────────────────────────────────

async def get_user_by_email(email: str, db: AsyncSession) -> User | None:
    result = await db.execute(
        select(User)
        .where(User.email == email, User.is_active == True)          # noqa: E712
        .options(
            selectinload(User.student_profile),
            selectinload(User.faculty_profile),
        )
    )
    return result.scalar_one_or_none()


async def get_user_by_id(user_id: uuid.UUID, db: AsyncSession) -> User | None:
    result = await db.execute(
        select(User)
        .where(User.id == user_id, User.is_active == True)           # noqa: E712
        .options(
            selectinload(User.student_profile),
            selectinload(User.faculty_profile),
        )
    )
    return result.scalar_one_or_none()


# ── Login ─────────────────────────────────────────────────────────────────────

async def login_with_google(id_token_str: str, db: AsyncSession) -> dict:
    claims = await verify_google_token(id_token_str)
    email: str = claims["email"]

    user = await get_user_by_email(email, db)
    if not user:
        raise ForbiddenError(
            "Your account is not registered in this system. "
            "Please contact your HOD or administrator."
        )

    token = create_access_token(user.id, user.role, user.email)
    return {
        "access_token": token,
        "token_type":   "bearer",
        "role":         user.role,
        "full_name":    user.full_name,
        "email":        user.email,
    }


# ── Logout ────────────────────────────────────────────────────────────────────

async def logout_user(token: str, db: AsyncSession) -> None:
    """
    Invalidate a JWT by adding its JTI to the blocklist.
    The token is decoded (without full verification) to extract JTI and
    expiry — the dependency already verified it before calling this.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        # Already invalid — logout is effectively a no-op
        return

    jti = payload.get("jti")
    if not jti:
        return   # Legacy token without jti claim

    exp_timestamp = payload.get("exp")
    if not exp_timestamp:
        return

    expires_at = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
    await block_token(jti=jti, expires_at=expires_at, db=db)
