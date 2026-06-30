"""
app/schemas/system_config.py
"""
from datetime import datetime

from pydantic import BaseModel, Field


class SystemConfigResponse(BaseModel):
    max_leave_days: int
    updated_at: datetime

    model_config = {"from_attributes": True}


class SystemConfigUpdate(BaseModel):
    max_leave_days: int = Field(..., ge=1, le=365)
