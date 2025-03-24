from sqlalchemy.orm import declarative_base
from sqlalchemy import Integer, Sequence, CHAR, Column, String, DateTime, Index, DECIMAL
import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "USER"

    USER_ID = Column(String(50), primary_key=True)                                  # USER_ID 컬럼
    USER_NAME = Column(String(50), index=True, nullable=False)                      # USER_NAME 컬럼
    PASSWORD = Column(String(100), nullable=False)                                   # PASSWORD 컬럼
    DEVICE_ID = Column(String(50), nullable=False)                                  # DEVICE_ID 컬럼
    REG_DT = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)     # 등록일 자동 생성
    MOD_DT = Column(DateTime)                                                       # 수정일


class Account(Base):
    __tablename__ = "ACCOUNT"

    ACCOUNT_ID = Column(Integer, Sequence('account_id_seq'), primary_key=True)   # 자동 증가 ID 컬럼
    USER_ID = Column(String(50), nullable=False, primary_key=True)               # 사용자
    ACCOUNT_NO = Column(String(10), nullable=False)                              # 계좌 번호(10자리)
    AUTH_ID = Column(Integer, nullable=False)                                    # AUTH_ID
    REG_DT = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)  # 등록일 자동 생성
    MOD_DT = Column(DateTime)  # 수정일


class Auth(Base):
    __tablename__ = "AUTH_KEY"

    AUTH_ID = Column(Integer, Sequence('auth_id_seq'), primary_key=True)         # 자동 증가 ID 컬럼
    USER_ID = Column(String(50), nullable=False, primary_key=True)               # 사용자 ID
    SIMULATION_YN = Column(CHAR(1), default='N', nullable=False)                 # 모의 투자 여부(Y: 모의투자, N: 실전투자)
    API_KEY = Column(String(50), nullable=False)                                 # API_KEY 컬럼
    SECRET_KEY = Column(String(50), nullable=False)                              # SECRET_KEY 컬럼
    REG_DT = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)  # 등록일 자동 생성
    MOD_DT = Column(DateTime)                                                    # 수정일

class Stocks(Base):
    __tablename__ = "STOCK_INFO"

    ST_CODE = Column(String(50), nullable=False, comment='단축 코드', primary_key=True)     # ST_CODE 컬럼
    SD_CODE = Column(String(50), nullable=False, comment='표준 코드', primary_key=True)     # SD_CODE 컬럼
    NAME = Column(String(100), nullable=False, comment='종목명', index=True)               # 주식 이름
    REG_DT = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)           # 등록일 자동 생성

    __table_args__ = (
        Index('ix_name_fulltext', 'NAME', mysql_prefix='FULLTEXT'),
        {'mysql_charset': 'utf8', 'mysql_collate': 'utf8_unicode_ci'}
    )


class Swing(Base):
    __tablename__ = "SWING_TRADE"

    SWING_ID = Column(Integer, Sequence('swing_id_seq'), primary_key=True)  # 자동 증가 ID 컬럼
    ACCOUNT_NO = Column(String(50), nullable=False)                         # 계좌 번호(10 자리)
    STOCK_CODE = Column(String(20), nullable=False)                         # 주식 종목 코드
    USE_YN = Column(CHAR(1), nullable=False)                                # 사용 여부
    SWING_AMOUNT = Column(DECIMAL(15, 2), nullable=False)     # 초기 투자금
    SWING_TYPE = Column(CHAR(1), nullable=False)                             # 스윙 타입 (D: 일봉, M: 분봉)
    SHORT_TERM = Column(Integer, nullable=False)                            # 단기 이평선
    MEDIUM_TERM = Column(Integer, nullable=False)                           # 중기 이평선
    LONG_TERM = Column(Integer, nullable=False)                             # 장기 이평선
    BUY_RATIO = Column(Integer, nullable=False)         # 매수 비율
    SELL_RATIO = Column(Integer, nullable=False)        # 매도 비율
    CROSS_TYPE = Column(CHAR(1), nullable=False)                            # 크로스 타입 (R: 추세 반전, S: 강한 추세)
    REG_DT = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    MOD_DT = Column(DateTime)