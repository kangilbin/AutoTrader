from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, String, DateTime, CHAR, DECIMAL, Integer, Sequence
import datetime

Base = declarative_base()

# 스윙 트레이드
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



