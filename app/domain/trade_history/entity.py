"""
TradeHistory 도메인 엔티티 - ORM 모델 + 비즈니스 로직
"""
from sqlalchemy import Column, Integer, String, CHAR, DECIMAL, DateTime, Sequence
from datetime import datetime

from app.common.database import Base


class TradeHistory(Base):
    """거래 내역 엔티티"""
    __tablename__ = "TRADE_HISTORY"

    TRADE_ID = Column(Integer, Sequence('trade_id_seq'), primary_key=True, comment='거래 ID')
    SWING_ID = Column(Integer, nullable=False, comment='스윙 ID')
    TRADE_DATE = Column(DateTime, nullable=False, comment='거래 일자')
    TRADE_TYPE = Column(CHAR(1), nullable=False, comment='거래 타입 (B: 매수, S: 매도)')
    TRADE_PRICE = Column(DECIMAL(15, 2), nullable=False, comment='거래 가격')
    TRADE_QTY = Column(Integer, nullable=False, comment='거래 수량')
    TRADE_AMOUNT = Column(DECIMAL(15, 2), nullable=False, comment='거래 금액')
    TOTAL_FEE = Column(DECIMAL(15, 2), nullable=True, comment='제비용합계 (수수료+세금, 매도 시)')
    REALIZED_PNL = Column(DECIMAL(15, 2), nullable=True, comment='실현손익 (매도 시)')
    # 저장 형식: [조건...] + [투입/회수 x%] + [진행 y%]  (조립 지점: order_executor)
    #   매수 단일  ["EMA돌파", "투입 33.5%"]
    #   매수 분할  ["EMA돌파", "투입 11.2%", "진행 33%"] / 후속 chunk는 조건 없이 ["투입...", "진행..."]
    #   매도 부분  ["부분 매도", "부분익절(+8%)", "목표가 533.20", "회수 35.2%"]
    #   매도 전량  ["전량 매도", "이익확정", "청산선 500.00", "손익 +5.2%", "회수 71.4%"]
    # 매수에 동작 라벨이 없는 이유: 진입이 1회뿐이라 TRADE_TYPE='B'에서 파생되는 정보.
    # 매도의 부분/전량은 다른 컬럼으로 복원할 수 없어 남긴다.
    # 투입·회수는 INIT_AMOUNT 대비(분모 동일 → 직접 비교 가능), 진행은 이번 목표 대비.
    TRADE_REASONS = Column(String(500), nullable=True,
                           comment='매매 사유 JSON ["전량 매도","이익확정","청산선 500.00","손익 +5.2%","회수 71.4%"]')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
