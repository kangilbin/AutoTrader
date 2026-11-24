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
    TRADE_REASONS = Column(String(500), nullable=True, comment='매매 사유 JSON ["추세약화","추세반전","EMA 이탈"]')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
