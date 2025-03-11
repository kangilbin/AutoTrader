from sqlalchemy.orm import declarative_base
from sqlalchemy import Integer, String, Sequence, Column
import datetime

Base = declarative_base()

class Account(Base):
    __tablename__ = "ACCOUNT"

    ACCOUNT_ID = Column(Integer, Sequence('account_id_seq'), nullable=False, unique=True, index=True)  # 자동 증가 ID 컬럼
    USER_ID = Column(Integer, primary_key=True, index=True)  # 사용자
    CANO = Column(String, index=True, nullable=False)        # 계좌 번호(앞 8자리)
    ACNT_PRDT_CD = Column(String, nullable=False)            # 계좌 번호(뒤 2자리)
    REG_DT = Column(String, default=datetime.datetime.utcnow, nullable=False)  # 등록일 자동 생성
