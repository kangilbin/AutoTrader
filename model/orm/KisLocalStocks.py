from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, String

Base = declarative_base()


class KisLocalStocks(Base):
    __tablename__ = "KIS_LOCAL_STOCKS"

    USER_ID = Column(String, primary_key=True, index=True)  # USER_ID 컬럼
    USER_NAME = Column(String, index=True, nullable=False)  # USER_NAME 컬럼
    PASSWORD = Column(String, nullable=False)  # PASSWORD 컬럼
    DEVICE_ID = Column(String, nullable=False)  # DEVICE_ID 컬럼
    API_KEY = Column(String, nullable=False)  # API_KEY 컬럼
    SECRET_KEY = Column(String, nullable=False)  # SECRET_KEY 컬럼
