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

    model_config = {
        "from_attributes": True,  # SQLAlchemy 모델에서 자동으로 변환
        "populate_by_name": True,  # 필드 이름으로 자동 매핑
        "json_encoders": {
            datetime: lambda dt: dt.isoformat() if dt else None  # datetime 변환 함수 지정
        }
    } 


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

    model_config = {
        "from_attributes": True,  # SQLAlchemy 모델에서 자동으로 변환
        "populate_by_name": True,  # 필드 이름으로 자동 매핑
        "json_encoders": {
            datetime: lambda dt: dt.isoformat() if dt else None  # datetime 변환 함수 지정
        }
    } 

