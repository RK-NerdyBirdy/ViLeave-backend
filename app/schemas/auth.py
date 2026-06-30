"""
app/schemas/auth.py
"""
from app.models.user import UserRole
from pydantic import BaseModel


class GoogleTokenRequest(BaseModel):
    id_token: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: UserRole
    full_name: str
    email: str
