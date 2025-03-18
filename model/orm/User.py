from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, String, DateTime
import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "USER"

    USER_ID = Column(String(50), primary_key=True)  # USER_ID 컬럼
    USER_NAME = Column(String(50), index=True, nullable=False)  # USER_NAME 컬럼
    PASSWORD = Column(String(30), nullable=False)  # PASSWORD 컬럼
    DEVICE_ID = Column(String(50), nullable=False)  # DEVICE_ID 컬럼
    API_KEY = Column(String(50), nullable=False)  # API_KEY 컬럼
    SECRET_KEY = Column(String(50), nullable=False)  # SECRET_KEY 컬럼
    REG_DT = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)  # 등록일 자동 생성
    MOD_DT = Column(DateTime)  # 수정일
