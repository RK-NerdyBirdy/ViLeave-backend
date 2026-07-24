"""
app/schemas/__init__.py
"""
from app.schemas.auth import TokenResponse, GoogleTokenRequest
from app.schemas.user import (
    StudentProfile, FacultyProfile,
    StudentCreate, FacultyCreate,
    StudentUpdate, FacultyUpdate,
    StudentListItem, FacultyListItem,
    PaginatedStudents, PaginatedFaculty,
    BulkIngestResponse, BulkIngestError,
)
from app.schemas.leave_request import (
    LeaveRequestCreate, LeaveRequestResponse,
    LeaveRequestUpdate, PaginatedLeaveRequests,
    ProctorDecision, HODDecision, HODSpecialDecision,
)
from app.schemas.system_config import SystemConfigResponse, SystemConfigUpdate

__all__ = [
    "TokenResponse", "GoogleTokenRequest",
    "StudentProfile", "FacultyProfile",
    "StudentCreate", "FacultyCreate",
    "StudentUpdate", "FacultyUpdate",
    "StudentListItem", "FacultyListItem",
    "PaginatedStudents", "PaginatedFaculty",
    "BulkIngestResponse", "BulkIngestError",
    "LeaveRequestCreate", "LeaveRequestResponse",
    "LeaveRequestUpdate", "PaginatedLeaveRequests",
    "ProctorDecision", "HODDecision", "HODSpecialDecision",
    "SystemConfigResponse", "SystemConfigUpdate",
]
