"""
User DTO (Data Transfer Object)
- Request/Response 스키마
"""
from datetime import datetime
from pydantic import BaseModel
from typing import Optional


class UserCreateRequest(BaseModel):
    """회원 가입 요청"""
    USER_ID: str
    USER_NAME: str
    PHONE: str
    PASSWORD: str


class UserLoginRequest(BaseModel):
    """로그인 요청"""
    USER_ID: str
    PASSWORD: str


class UserUpdateRequest(BaseModel):
    """회원 정보 수정 요청"""
    USER_NAME: Optional[str] = None
    PHONE: Optional[str] = None
    PASSWORD: Optional[str] = None


class UserResponse(BaseModel):
    """회원 정보 응답"""
    USER_ID: str
    USER_NAME: Optional[str] = None
    PHONE: Optional[str] = None
    REG_DT: Optional[datetime] = None
    MOD_DT: Optional[datetime] = None

    model_config = {
        "from_attributes": True,
        "json_encoders": {
            datetime: lambda dt: dt.isoformat() if dt else None
        }
    }


class TokenResponse(BaseModel):
    """토큰 응답"""
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"