"""
Auth DTO (Data Transfer Object)
"""
from datetime import datetime
from pydantic import BaseModel
from typing import Optional


class AuthCreateRequest(BaseModel):
    """인증키 생성 요청"""
    AUTH_NAME: str
    SIMULATION_YN: str
    API_KEY: str
    SECRET_KEY: str


class AuthUpdateRequest(BaseModel):
    """인증키 수정 요청"""
    AUTH_NAME: Optional[str] = None
    SIMULATION_YN: Optional[str] = None
    API_KEY: Optional[str] = None
    SECRET_KEY: Optional[str] = None


class AuthChoiceRequest(BaseModel):
    """인증키 선택 요청"""
    AUTH_ID: int
    ACCOUNT_NO: str


class AuthResponse(BaseModel):
    """인증키 응답 (API_KEY, SECRET_KEY 제외)"""
    AUTH_ID: int
    AUTH_NAME: Optional[str] = None
    USER_ID: Optional[str] = None
    SIMULATION_YN: Optional[str] = None
    REG_DT: Optional[datetime] = None
    MOD_DT: Optional[datetime] = None

    model_config = {
        "from_attributes": True,
        "json_encoders": {
            datetime: lambda dt: dt.isoformat() if dt else None
        }
    }