"""
app/schemas/auth.py
"""
from typing import List
from app.models.user import UserRole
from pydantic import BaseModel


class GoogleTokenRequest(BaseModel):
    id_token: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    active_role: UserRole      # Renamed from 'role'
    full_name: str
    email: str


class UserProfileResponse(BaseModel):
    """Schema for the GET /auth/me endpoint"""
    id: str
    email: str
    full_name: str
    active_role: UserRole
    all_roles: List[UserRole]
    is_active: bool