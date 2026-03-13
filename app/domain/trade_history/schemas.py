"""
Trade History Schemas - Request/Response DTO
"""
from pydantic import BaseModel, ConfigDict
from datetime import datetime
from decimal import Decimal
from typing import Optional, List


class TradeHistoryResponse(BaseModel):
    """거래 내역 응답"""
    TRADE_ID: int
    SWING_ID: int
    TRADE_DATE: datetime
    TRADE_TYPE: str  # "B": 매수, "S": 매도
    TRADE_PRICE: Decimal
    TRADE_QTY: int
    TRADE_AMOUNT: Decimal
    TRADE_REASONS: Optional[str] = None  # JSON 문자열
    REG_DT: datetime

    model_config = ConfigDict(from_attributes=True)


class PriceHistoryItem(BaseModel):
    """일별 주가 데이터"""
    STCK_BSOP_DATE: str
    STCK_OPRC: Decimal
    STCK_HGPR: Decimal
    STCK_LWPR: Decimal
    STCK_CLPR: Decimal
    ACML_VOL: int


class Ema20HistoryItem(BaseModel):
    """EMA20 데이터"""
    STCK_BSOP_DATE: str
    ema20: Optional[float] = None


class TradeHistoryWithChartResponse(BaseModel):
    """매매 내역 + 차트 데이터 응답"""
    swing_id: int
    st_code: str
    year: int
    trades: List[TradeHistoryResponse]
    price_history: List[PriceHistoryItem]
    ema20_history: List[Ema20HistoryItem]


class TradeHistoryCreateRequest(BaseModel):
    """거래 내역 생성 요청"""
    SWING_ID: int
    TRADE_TYPE: str
    TRADE_PRICE: Decimal
    TRADE_QTY: int
    TRADE_AMOUNT: Decimal
    TRADE_REASONS: Optional[list[str]] = None