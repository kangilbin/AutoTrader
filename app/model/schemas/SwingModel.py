from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class SwingCreate(BaseModel):
    SWING_ID: int
    USER_ID: Optional[str] = None
    ACCOUNT_NO: Optional[str] = None
    ST_CODE: Optional[str] = None
    USE_YN: Optional[str] = None
    SWING_AMOUNT: Optional[float] = None
    SWING_TYPE: Optional[str] = None
    SHORT_TERM: Optional[int] = None
    MEDIUM_TERM: Optional[int] = None
    LONG_TERM: Optional[int] = None
    BUY_RATIO: Optional[int] = None
    SELL_RATIO: Optional[int] = None
    CROSS_TYPE: Optional[str] = None
    REG_DT: Optional[datetime] = None
    MOD_DT: Optional[datetime] = None

class SwingResponse(SwingCreate):
    class Config:
        model_config = {"from_attributes": True} 