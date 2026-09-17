"""
Account DTO (Data Transfer Object)
"""
from datetime import datetime
from pydantic import BaseModel
from typing import List, Optional


class AccountVerifyRequest(BaseModel):
    """계좌번호 검증 요청"""
    ACCOUNT_NO: str
    AUTH_ID: int


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


class SwingImpactItem(BaseModel):
    """삭제로 함께 사라지는 스윙 1건"""
    SWING_ID: int
    ACCOUNT_NO: str          # 인증키 삭제는 여러 계좌에 걸치므로 항목별 귀속이 필요
    ST_CODE: str
    MRKT_CODE: str
    HOLD_QTY: int = 0
    SIGNAL: int = 0
    HAS_POSITION: bool = False

    model_config = {"from_attributes": True}


class DeleteImpactResponse(BaseModel):
    """삭제 영향도 (삭제 전 확인용)

    HAS_POSITION이 참이면 보유 중인 주식이 있다는 뜻이다. 삭제해도 주식은
    증권사 계좌에 그대로 남고 손절·익절 자동 평가만 멈추므로, 클라이언트는
    단순 확인이 아니라 경고를 띄워야 한다.
    """
    ACCOUNT_NOS: List[str] = []
    SWINGS: List[SwingImpactItem] = []
    HAS_POSITION: bool = False
