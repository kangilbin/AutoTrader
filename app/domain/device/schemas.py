"""
Device 스키마 - Request/Response DTO
"""
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from datetime import datetime


class DeviceCreateRequest(BaseModel):
    """디바이스 생성 요청"""
    DEVICE_ID: str = Field(..., description="디바이스 ID")
    DEVICE_NAME: str = Field(..., description="디바이스 이름")
    USER_ID: Optional[str] = Field(None, description="사용자 ID (NULL=공용)")


class DeviceUpdateRequest(BaseModel):
    """디바이스 수정 요청"""
    DEVICE_NAME: Optional[str] = Field(None, description="디바이스 이름")
    USER_ID: Optional[str] = Field(None, description="사용자 ID (NULL=공용)")
    ACTIVE_YN: Optional[str] = Field(None, description="활성 여부 (Y/N)")


class DeviceResponse(BaseModel):
    """디바이스 응답"""
    DEVICE_ID: str
    DEVICE_NAME: str
    USER_ID: Optional[str] = None
    ACTIVE_YN: str
    REG_DT: datetime
    MOD_DT: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)