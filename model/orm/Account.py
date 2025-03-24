from sqlalchemy.orm import declarative_base
from sqlalchemy import Integer, String, Sequence, Column, CHAR, DateTime
import datetime

Base = declarative_base()


class Account(Base):
    __tablename__ = "ACCOUNT"

    ACCOUNT_ID = Column(Integer, Sequence('account_id_seq'), primary_key=True)   # 자동 증가 ID 컬럼
    USER_ID = Column(String(50), nullable=False, primary_key=True)               # 사용자
    ACCOUNT_NO = Column(String(10), nullable=False)                              # 계좌 번호(10자리)
    REG_DT = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)  # 등록일 자동 생성
    MOD_DT = Column(DateTime)  # 수정일
