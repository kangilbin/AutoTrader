"""
Notification 도메인 Schemas - Request/Response DTO
"""
from pydantic import BaseModel, ConfigDict
from typing import Optional


class NotiSettingItem(BaseModel):
    """알림 설정 항목"""
    NOTI_TYPE: str
    USE_YN: str

    model_config = ConfigDict(from_attributes=True)


class NotiSettingUpdateRequest(BaseModel):
    """알림 설정 변경 요청 (개별 항목)"""
    NOTI_TYPE: str   # 'BUY', 'SELL', 'SIGNAL' 등
    USE_YN: str      # 'Y' or 'N'


class PushTokenRegisterRequest(BaseModel):
    """푸쉬 토큰 등록 요청"""
    PUSH_TOKEN: str
    DEVICE_TYPE: Optional[str] = None


class PushTokenDeleteRequest(BaseModel):
    """푸쉬 토큰 삭제 요청"""
    PUSH_TOKEN: str