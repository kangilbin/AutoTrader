from sqlalchemy.orm import declarative_base
from sqlalchemy import Integer, Sequence, CHAR, Column, String, DateTime, Index, DECIMAL
from datetime import datetime

Base = declarative_base()

class User(Base):
    """"
    계정 테이블 정의
    """
    __tablename__ = "USER"

    USER_ID = Column(String(50), primary_key=True, comment='사용자 ID')
    USER_NAME = Column(String(50), nullable=False, comment='사용자 이름')
    PHONE = Column(CHAR(11), nullable=False, comment='휴대폰 번호')
    PASSWORD = Column(String(100), nullable=False, comment='비밀 번호')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')


class Account(Base):
    """"
    계좌 테이블 정의
    """
    __tablename__ = "ACCOUNT"

    ACCOUNT_ID = Column(Integer, Sequence('account_id_seq'), primary_key=True, comment='ACCOUNT ID')
    USER_ID = Column(String(50), nullable=False, primary_key=True, comment='사용자 ID')
    ACCOUNT_NO = Column(String(10), nullable=False, comment='계좌 번호')
    AUTH_ID = Column(Integer, nullable=False, comment='권한 ID')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')


class Auth(Base):
    """"
    권한 테이블 정의
    """
    __tablename__ = "AUTH_KEY"

    AUTH_ID = Column(Integer, Sequence('auth_id_seq'), primary_key=True, comment='권한 ID')
    USER_ID = Column(String(50), nullable=False, primary_key=True, comment='사용자 ID')
    AUTH_NAME = Column(String(50), nullable=False, comment='권한 이름')
    SIMULATION_YN = Column(CHAR(1), default='N', nullable=False, comment='모의 투자 여부(Y: 모의투자, N: 실전투자)')
    API_KEY = Column(String(200), nullable=False, comment='앱키 키')
    SECRET_KEY = Column(String(350), nullable=False, comment='시크릿 키')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')


class Stock(Base):
    """"
    주식 정보 테이블 정의
    """
    __tablename__ = "STOCK_INFO"

    ST_CODE = Column(String(50), nullable=False, comment='종목 코드', primary_key=True)
    SD_CODE = Column(String(50), nullable=False, comment='주식 표준 코드')
    NAME = Column(String(100), nullable=False, comment='종목명')
    DATA_YN = Column(CHAR(1), nullable=False, default='N', comment='데이터 적재 여부')
    DEL_YN = Column(CHAR(1), nullable=False, default='N', comment='상장 폐지 여부')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')


class StockHstr(Base):
    """"
    주식 일일 데이터 내역 테이블 정의
    """
    __tablename__ = "STOCK_DAY_HISTORY"

    ST_CODE = Column(String(50), nullable=False, primary_key=True, comment='종목 코드')
    STCK_BSOP_DATE = Column(String(8), nullable=False, primary_key=True, comment='주식 영업 일자')
    STCK_OPRC = Column(DECIMAL(15, 2), nullable=False, comment='주식 시가')
    STCK_HGPR = Column(DECIMAL(15, 2), nullable=False, comment='주식 최고가')
    STCK_LWPR = Column(DECIMAL(15, 2), nullable=False, comment='주식 최저가')
    STCK_CLPR = Column(DECIMAL(15, 2), nullable=False, comment='주식 종가')
    ACML_VOL = Column(Integer, nullable=False, comment='누적 거래량')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')


class Swing(Base):
    """"
    스윙 테이블 정의
    """
    __tablename__ = "SWING_TRADE"
    SWING_ID = Column(Integer, Sequence('swing_id_seq'), primary_key=True, comment='스윙 ID')
    ACCOUNT_NO = Column(String(50), nullable=False, primary_key=True, comment='계좌 번호')
    ST_CODE = Column(String(50), nullable=False, primary_key=True, comment='종목 코드')
    # USE_YN = Column(CHAR(1), nullable=False, default='L', comment='L: 국내, F: 해외')
    USE_YN = Column(CHAR(1), nullable=False, default='Y', comment='사용 여부')
    INIT_AMOUNT = Column(DECIMAL(15, 2), nullable=False, comment='초기 투자금')
    CUR_AMOUNT = Column(DECIMAL(15, 2), nullable=False, comment='현재 투자금')
    SWING_TYPE = Column(CHAR(1), nullable=False, comment='스윙 타입 (A: 이평선, B: 일목균형표)')
    BUY_RATIO = Column(Integer, nullable=False, comment='매수 비율')
    SELL_RATIO = Column(Integer, nullable=False, comment='매도 비율')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')

class EmaOpt(Base):
    """
    이평선 설정
    """
    __tablename__ = "EMA_OPT"
    ACCOUNT_NO = Column(String(50), nullable=False, primary_key=True, comment='계좌 번호')
    ST_CODE = Column(String(50), nullable=False, primary_key=True, comment='종목 코드')
    SHORT_TERM = Column(Integer, nullable=False, comment='단기 이평선')
    MEDIUM_TERM = Column(Integer, nullable=False, comment='중기 이평선')
    LONG_TERM = Column(Integer, nullable=False, comment='장기 이평선')



class TradeHistory(Base):
    """"
    거래 내역 테이블 정의
    """
    __tablename__ = "TRADE_HISTORY"

    TRADE_ID = Column(Integer, Sequence('trade_id_seq'), primary_key=True, comment='거래 ID')
    SWING_ID = Column(Integer, nullable=False, comment='스윙 ID')
    TRADE_DATE = Column(DateTime, nullable=False, comment='거래 일자')
    TRADE_TYPE = Column(CHAR(1), nullable=False, comment='거래 타입 (B: 매수, S: 매도)')
    TRADE_PRICE = Column(DECIMAL(15, 2), nullable=False, comment='거래 가격')
    TRADE_QTY = Column(Integer, nullable=False, comment='거래 수량')
    TRADE_AMOUNT = Column(DECIMAL(15, 2), nullable=False, comment='거래 금액')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')

