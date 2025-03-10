from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, String

Base = declarative_base()

class Account(Base):
    __tablename__ = "ACCOUNT"

    USER_ID = Column(Integer, primary_key=True, index=True) # 사용자
    CANO = Column(String, index=True, nullable=False)       # 계좌 번호(앞 8자리)
    ACNT_PRDT_CD = Column(String, nullable=False)           # 계좌 번호(뒤 2자리)
    REG_DT = Column(String, nullable=False)                 # 등록일

