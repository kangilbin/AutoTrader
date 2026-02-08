"""
Device 스키마 - Request/Response DTO
"""
from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime


class DeviceResponse(BaseModel):
    """디바이스 응답"""
    DEVICE_ID: str
    DEVICE_NAME: str
    USER_ID: Optional[str] = None
    ACTIVE_YN: str
    REG_DT: datetime
    MOD_DT: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)