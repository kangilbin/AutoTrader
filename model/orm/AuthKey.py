from sqlalchemy.orm import declarative_base
from sqlalchemy import Integer, String, Sequence, Column, CHAR, DateTime
import datetime

Base = declarative_base()


class Auth(Base):
    __tablename__ = "AUTH_KEY"

    AUTH_ID = Column(Integer, Sequence('auth_id_seq'), primary_key=True)         # 자동 증가 ID 컬럼
    USER_ID = Column(String(50), nullable=False, primary_key=True)               # 사용자 ID
    SIMULATION_YN = Column(CHAR(1), default='N', nullable=False)                 # 모의 투자 여부(Y: 모의투자, N: 실전투자)
    API_KEY = Column(String(50), nullable=False)                                 # API_KEY 컬럼
    SECRET_KEY = Column(String(50), nullable=False)                              # SECRET_KEY 컬럼
    REG_DT = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)  # 등록일 자동 생성
    MOD_DT = Column(DateTime)                                                    # 수정일
