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


class IchimokuHistoryItem(BaseModel):
    """일목균형표 — 날짜별 값은 모두 그 날짜에 **그려지는 위치** 기준이다 (프론트가 날짜만 맞춰 선을 잇는다)

    senkou_a·senkou_b 는 표준대로 26봉 앞에 그려지는 값이라 그 날짜 칸에는 26봉 전에 계산한 값이 들어 있다 (두 선 사이가 구름,
    senkou_a > senkou_b 면 양운). chikou 는 26봉 뒤 종가를 그 날짜에 둔 후행스팬이라 마지막 26봉은 None 이다. 계산 전 구간은 None.
    """
    STCK_BSOP_DATE: str
    tenkan: Optional[float] = None
    kijun: Optional[float] = None
    senkou_a: Optional[float] = None
    senkou_b: Optional[float] = None
    cloud_top: Optional[float] = None
    cloud_bot: Optional[float] = None
    chikou: Optional[float] = None


class IchimokuFutureItem(BaseModel):
    """마지막 봉 뒤 26봉의 구름 — 날짜는 영업일로 **추정**한 값이라 휴장일이 있으면 실제 거래일과 어긋날 수 있다"""
    STCK_BSOP_DATE: str
    senkou_a: float
    senkou_b: float
    cloud_top: float
    cloud_bot: float
    estimated: bool = True


class TradeHistoryWithChartResponse(BaseModel):
    """매매 내역 + 차트 데이터 응답

    swing_type 으로 오버레이를 고른다: 'S'(단일 EMA)는 ema20_history, 'I'(일목균형표)는 ichimoku_history/ichimoku_future.
    쓰지 않는 쪽은 빈 목록이다 (일목 스윙의 ema20_history 는 비어 있다).
    """
    swing_id: int
    st_code: str
    swing_type: str = "S"
    start_date: str
    end_date: str
    trades: List[TradeHistoryResponse]
    price_history: List[PriceHistoryItem]
    ema20_history: List[Ema20HistoryItem]
    ichimoku_history: List[IchimokuHistoryItem] = []
    ichimoku_future: List[IchimokuFutureItem] = []


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