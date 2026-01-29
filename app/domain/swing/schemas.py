"""
Swing DTO (Data Transfer Object)
"""
from datetime import datetime
from pydantic import BaseModel
from typing import Optional
from decimal import Decimal


class SwingCreateRequest(BaseModel):
    """스윙 전략 등록 요청"""
    MRKT_CODE: str
    ST_CODE: str
    ACCOUNT_NO: str
    INIT_AMOUNT: int
    SWING_TYPE: str  # 'A': 이평선, 'B': 일목균형표
    BUY_RATIO: Optional[int] = 50
    SELL_RATIO: Optional[int] = 50
    # EMA 옵션 (SWING_TYPE이 'A'인 경우)
    SHORT_TERM: Optional[int] = 5
    MEDIUM_TERM: Optional[int] = 20
    LONG_TERM: Optional[int] = 60


class SwingUpdateRequest(BaseModel):
    """스윙 전략 수정 요청"""
    USE_YN: Optional[str] = None
    INIT_AMOUNT: Optional[int] = None
    SWING_TYPE: Optional[str] = None
    BUY_RATIO: Optional[int] = None
    SELL_RATIO: Optional[int] = None


class SwingResponse(BaseModel):
    """스윙 전략 응답"""
    SWING_ID: Optional[int] = None
    MRKT_CODE: Optional[str] = None
    ST_CODE: Optional[str] = None
    ST_NM: Optional[str] = None
    ACCOUNT_NO: Optional[str] = None
    USE_YN: Optional[str] = None
    INIT_AMOUNT: Optional[Decimal] = None
    CUR_AMOUNT: Optional[Decimal] = None
    SWING_TYPE: Optional[str] = None
    BUY_RATIO: Optional[int] = None
    SELL_RATIO: Optional[int] = None
    SIGNAL: Optional[int] = None
    REG_DT: Optional[datetime] = None
    MOD_DT: Optional[datetime] = None

    model_config = {
        "from_attributes": True,
        "json_encoders": {
            datetime: lambda dt: dt.isoformat() if dt else None
        }
    }


class SwingMappingResponse(BaseModel):
    """스윙 매핑 응답 (보유 주식 정보 포함)"""
    SWING_ID: Optional[int] = None
    MRKT_CODE: Optional[str] = None
    ST_CODE: Optional[str] = None
    ACCOUNT_NO: Optional[str] = None
    USE_YN: Optional[str] = None
    INIT_AMOUNT: Optional[Decimal] = None
    CUR_AMOUNT: Optional[Decimal] = None
    SWING_TYPE: Optional[str] = None
    SIGNAL: Optional[int] = None
    ST_NM: Optional[str] = None  # 종목명
    QTY: Optional[str] = None  # 보유 수량
    RATE: Optional[float] = None  # 수익률