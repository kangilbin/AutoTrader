from sqlalchemy.orm import declarative_base
from sqlalchemy import Integer, String, Sequence, Column, CHAR, DateTime
import datetime

Base = declarative_base()


class Account(Base):
    __tablename__ = "ACCOUNT"

    ACCOUNT_ID = Column(Integer, Sequence('account_id_seq'), primary_key=True)  # 자동 증가 ID 컬럼
    USER_ID = Column(String(50), nullable=False)                                    # 사용자
    CANO = Column(String(8), nullable=False)                                       # 계좌 번호(앞 8자리)
    ACNT_PRDT_CD = Column(String(2), nullable=False)                               # 계좌 번호(뒤 2자리)
    SIMULATION_YN = Column(CHAR(1), default='N', nullable=False)                 # 시뮬레이션 여부
    REG_DT = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)  # 등록일 자동 생성
    MOD_DT = Column(DateTime)  # 수정일
