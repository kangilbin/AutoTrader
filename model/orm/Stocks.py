from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, String, Index

Base = declarative_base()


class Stocks(Base):
    __tablename__ = "STOCK_INFO"

    ST_CODE = Column(String(50), nullable=False, comment='단축 코드', primary_key=True)  # ST_CODE 컬럼
    SD_CODE = Column(String(50), nullable=False, comment='표준 코드')  # SD_CODE 컬럼
    NAME = Column(String(100), nullable=False, comment='종목명', index=True)  # NAME 컬럼

    __table_args__ = (
        Index('ix_name_fulltext', 'NAME', mysql_prefix='FULLTEXT'),
        {'mysql_charset': 'utf8', 'mysql_collate': 'utf8_unicode_ci'}
    )