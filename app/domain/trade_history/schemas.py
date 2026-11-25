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
    TOTAL_FEE: Optional[Decimal] = None  # 제비용합계 (매도 시)
    REALIZED_PNL: Optional[Decimal] = None  # 실현손익 (매도 시)
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
    start_date: str
    end_date: str
    trades: List[TradeHistoryResponse]
    price_history: List[PriceHistoryItem]
    ema20_history: List[Ema20HistoryItem]


class TradeStatsResponse(BaseModel):
    """매매 통계 응답"""
    total_count: int
    buy_count: int
    sell_count: int


class TradeHistoryPageResponse(BaseModel):
    """매매 내역 페이징 응답"""
    trades: List[TradeHistoryResponse]
    total_count: int
    page: int
    size: int
    has_next: bool


class TradeHistoryCreateRequest(BaseModel):
    """거래 내역 생성 요청"""
    SWING_ID: int
    TRADE_TYPE: str
    TRADE_PRICE: Decimal
    TRADE_QTY: int
    TRADE_AMOUNT: Decimal
    TRADE_REASONS: Optional[list[str]] = None