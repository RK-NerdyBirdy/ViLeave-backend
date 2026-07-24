"""
app/schemas/system_config.py
"""
from datetime import date, datetime

from pydantic import BaseModel, Field


class SystemConfigResponse(BaseModel):
    max_od_days: int
    max_medical_days: int
    special_request_threshold_days: int
    cat_2_start_date: date
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class SystemConfigUpdate(BaseModel):
    max_od_days: int = Field(..., ge=1, le=365)
    max_medical_days: int = Field(..., ge=1, le=365)
    special_request_threshold_days: int = Field(..., ge=1, le=365)
    cat_2_start_date: date
