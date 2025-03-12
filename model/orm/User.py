from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, String

Base = declarative_base()

class User(Base):
    __tablename__ = "USER"

    USER_ID = Column(String, primary_key=True)  # USER_ID 컬럼
    USER_NAME = Column(String, index=True, nullable=False)  # USER_NAME 컬럼
    PASSWORD = Column(String, nullable=False)  # PASSWORD 컬럼
    DEVICE_ID = Column(String, nullable=False)  # DEVICE_ID 컬럼
    API_KEY = Column(String, nullable=False)  # API_KEY 컬럼
    SECRET_KEY = Column(String, nullable=False)  # SECRET_KEY 컬럼
