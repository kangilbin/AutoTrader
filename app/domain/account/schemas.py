"""
Account DTO (Data Transfer Object)
"""
from datetime import datetime
from pydantic import BaseModel
from typing import Optional


class AccountCreateRequest(BaseModel):
    """계좌 등록 요청"""
    ACCOUNT_NO: str
    AUTH_ID: int


class AccountUpdateRequest(BaseModel):
    """계좌 수정 요청"""
    ACCOUNT_NO: Optional[str] = None
    AUTH_ID: Optional[int] = None


class AccountResponse(BaseModel):
    """계좌 정보 응답"""
    ACCOUNT_ID: Optional[int] = None
    USER_ID: Optional[str] = None
    ACCOUNT_NO: Optional[str] = None
    AUTH_ID: Optional[int] = None
    SIMULATION_YN: Optional[str] = None
    REG_DT: Optional[datetime] = None
    MOD_DT: Optional[datetime] = None

    model_config = {
        "from_attributes": True,
        "populate_by_name": True,
        "json_encoders": {
            datetime: lambda dt: dt.isoformat() if dt else None
        }
    }


class AccountDetailResponse(BaseModel):
    """계좌 상세 정보 (Redis 캐시용)"""
    ACCOUNT_NO: str
    SIMULATION_YN: str
    API_KEY: str
    SECRET_KEY: str