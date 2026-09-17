"""
Trade History Schemas - Request/Response DTO
"""
from pydantic import BaseModel, ConfigDict
from datetime import datetime
from decimal import Decimal
from typing import Optional


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


class TradeHistoryCreateRequest(BaseModel):
    """거래 내역 생성 요청"""
    SWING_ID: int
    TRADE_TYPE: str
    TRADE_PRICE: Decimal
    TRADE_QTY: int
    TRADE_AMOUNT: Decimal
    TRADE_REASONS: Optional[list[str]] = None