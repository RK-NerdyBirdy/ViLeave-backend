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
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
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
GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


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


def build_google_state(role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "nonce": str(uuid.uuid4()),
        "redirect": settings.oauth_success_redirect_url,
        "iat": now,
        "exp": now + timedelta(minutes=10),
        "role": role,  # Embed the requested role here
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def build_google_authorize_url(state: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
        "access_type": "offline",
        "include_granted_scopes": "true",
    }
    return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"


def build_oauth_redirect_url(access_token: str) -> str:
    fragment = urlencode({"access_token": access_token, "token_type": "bearer"})
    parts = urlsplit(settings.oauth_success_redirect_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, fragment))


def validate_google_state(state: str) -> dict:
    try:
        payload = jwt.decode(
            state,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise UnauthorizedError(f"Invalid OAuth state: {exc}") from exc

    redirect_target = payload.get("redirect")
    if redirect_target != settings.oauth_success_redirect_url:
        raise UnauthorizedError("Invalid OAuth state redirect target")
    
    return payload # Return the payload to extract the role


async def exchange_google_code(code: str) -> str:
    data = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": settings.google_redirect_uri,
        "grant_type": "authorization_code",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                GOOGLE_TOKEN_ENDPOINT,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "Google token exchange failed"
        raise UnauthorizedError(f"Google token exchange failed: {detail}") from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not reach Google during OAuth callback. Please try again shortly.",
        ) from exc

    payload = response.json()
    id_token = payload.get("id_token")
    if not id_token:
        raise UnauthorizedError("Google token exchange did not return an ID token")
    return id_token


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

# ── Login ─────────────────────────────────────────────────────────────────────

async def login_with_google(id_token_str: str, db: AsyncSession, requested_role: str) -> dict:
    claims = await verify_google_token(id_token_str)
    email: str = claims["email"]

    # 1. Early rejection for non-VIT domains
    if not (email.endswith("@vit.ac.in") or email.endswith("@vitstudent.ac.in") or email.endswith("@gmail.com")):
        raise ForbiddenError("Access denied. Only VIT domains are allowed.")

    # 2. Fetch user
    user = await get_user_by_email(email, db)
    if not user:
        raise ForbiddenError("Your account is not registered in this system.")

    # Convert their DB roles to a list of strings for easy checking
    user_role_values = [r.value for r in user.roles]

    # 3. STRICT CHECK: Is the requested portal inside their allowed roles?
    if requested_role not in user_role_values:
        raise ForbiddenError(
            f"Access denied. You do not have permissions for the '{requested_role}' portal. "
            "Please go back and select the correct login button."
        )

    # 4. Domain Validation
    if requested_role == UserRole.STUDENT.value and not email.endswith("@vitstudent.ac.in"):
        raise ForbiddenError("Students must use @vitstudent.ac.in.")
    elif requested_role in (UserRole.FACULTY.value, UserRole.HOD.value) and not (email.endswith("@vit.ac.in") or email.endswith("@gmail.com")):
        raise ForbiddenError("Faculty/HOD must use @vit.ac.in.")

    # 5. ISSUE A SCOPED TOKEN: 
    # We do NOT put all their roles in the token. We ONLY put the role they requested.
    # This guarantees they can't use a Proctor token to hit HOD endpoints.
    
    token = create_access_token(user.id, UserRole(requested_role), user.email)
    
    return {
        "access_token": token,
        "token_type":   "bearer",
        "active_role":  requested_role, # The portal they are currently logged into
        "full_name":    user.full_name,
        "email":        user.email,
    }


async def complete_google_login(code: str, state: str, db: AsyncSession) -> str:
    # Get the payload and extract the role
    state_payload = validate_google_state(state)
    requested_role = state_payload.get("role")
    
    if not requested_role:
        raise UnauthorizedError("Authentication state is missing role information.")

    id_token_str = await exchange_google_code(code)
    auth_result = await login_with_google(id_token_str, db, requested_role)
    return build_oauth_redirect_url(auth_result["access_token"])
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
