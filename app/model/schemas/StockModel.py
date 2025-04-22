from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class StockCreate(BaseModel):
    ST_CODE: str
    SD_CODE: Optional[str] = None
    NAME: Optional[str] = None
    DATA_YN: Optional[str] = None
    DEL_YN: Optional[str] = None
    REG_DT: Optional[datetime] = None
    MOD_DT: Optional[datetime] = None


class StockResponse(StockCreate):

    class Config:
        orm_mode = True


class StockHstrCreate(BaseModel):
    ST_CODE: str
    HSTR_DT: str
    DATE: str
    OPEN_PRICE: float
    HIGH_PRICE: float
    LOW_PRICE: float
    CLOSE_PRICE: float
    TRADE_QTY: int
    REG_DT: Optional[datetime] = None
    MOD_DT: Optional[datetime] = None


class StockHstrResponse(StockHstrCreate):

    class Config:
        orm_mode = True

