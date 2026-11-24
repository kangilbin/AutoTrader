"""
Stock 도메인 엔티티 - ORM 모델 + 비즈니스 로직
"""
from sqlalchemy import Column, Integer, String, CHAR, DECIMAL, DateTime
from datetime import datetime

from app.common.database import Base


class Stock(Base):
    """종목 정보 엔티티"""
    __tablename__ = "STOCK_INFO"

    MRKT_CODE = Column(String(50), nullable=False, primary_key=True, comment='조건 시장 분류 코드(J:KRX, NX:NXT, UN:통합)')
    ST_CODE = Column(String(50), nullable=False, primary_key=True, comment='종목 코드')
    SD_CODE = Column(String(50), nullable=False, comment='주식 표준 코드')
    ST_NM = Column(String(100), nullable=False, comment='종목명')
    DATA_YN = Column(CHAR(1), nullable=False, default='N', comment='데이터 적재 여부')
    DEL_YN = Column(CHAR(1), nullable=False, default='N', comment='상장 폐지 여부')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')


class StockHistory(Base):
    """주식 일별 데이터 엔티티"""
    __tablename__ = "STOCK_DAY_HISTORY"

    MRKT_CODE = Column(String(50), nullable=False, primary_key=True, comment='조건 시장 분류 코드(J:KRX, NX:NXT, UN:통합)')
    ST_CODE = Column(String(50), nullable=False, primary_key=True, comment='종목 코드')
    STCK_BSOP_DATE = Column(String(8), nullable=False, primary_key=True, comment='주식 영업 일자')
    STCK_OPRC = Column(DECIMAL(15, 2), nullable=False, comment='주식 시가')
    STCK_HGPR = Column(DECIMAL(15, 2), nullable=False, comment='주식 최고가')
    STCK_LWPR = Column(DECIMAL(15, 2), nullable=False, comment='주식 최저가')
    STCK_CLPR = Column(DECIMAL(15, 2), nullable=False, comment='주식 종가')
    ACML_VOL = Column(Integer, nullable=False, comment='누적 거래량')
    FRGN_NTBY_QTY = Column(Integer, nullable=True, comment='외국인 순매수 수량')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')
