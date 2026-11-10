"""
Swing DTO (Data Transfer Object)
"""
from datetime import datetime
from pydantic import BaseModel
from typing import Literal, Optional
from decimal import Decimal


class SwingCreateRequest(BaseModel):
    """스윙 전략 등록 요청"""
    MRKT_CODE: str
    ST_CODE: str
    ACCOUNT_NO: str
    SWING_TYPE: str  # 'A': 이평선, 'B': 일목균형표
    # 백테스트 입력 전용 — 스윙 등록은 이 값을 저장하지 않는다 (매수는 총자산 기준 풀 방식)
    INIT_AMOUNT: int
    FULL_ENTRY_YN: Literal['Y', 'N'] = 'N'  # 백테스트 사이징 옵션: 'Y' 전량, 'N' 신호 강도 기반
    # EMA 옵션 (SWING_TYPE이 'A'인 경우)
    SHORT_TERM: Optional[int] = 5
    MEDIUM_TERM: Optional[int] = 20
    LONG_TERM: Optional[int] = 60


class SwingUpdateRequest(BaseModel):
    """스윙 전략 수정 요청"""
    USE_YN: Optional[str] = None
    SWING_TYPE: Optional[str] = None


class SwingResponse(BaseModel):
    """스윙 전략 응답"""
    SWING_ID: Optional[int] = None
    MRKT_CODE: Optional[str] = None
    ST_CODE: Optional[str] = None
    ST_NM: Optional[str] = None
    ACCOUNT_NO: Optional[str] = None
    USE_YN: Optional[str] = None
    ENTRY_PRICE: Optional[Decimal] = None
    HOLD_QTY: Optional[int] = None
    SWING_TYPE: Optional[str] = None
    SIGNAL: Optional[int] = None
    REG_DT: Optional[datetime] = None
    MOD_DT: Optional[datetime] = None

    model_config = {
        "from_attributes": True,
        "json_encoders": {
            datetime: lambda dt: dt.isoformat() if dt else None
        }
    }

