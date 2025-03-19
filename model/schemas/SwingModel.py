from pydantic import BaseModel

class SwingCreate(BaseModel):
    ACCOUNT_NO: str
    STOCK_CODE: str
    USE_YN: str
    SWING_AMOUNT: float
    SWING_TYPE: str
    SHORT_TERM: int
    MEDIUM_TERM: int
    LONG_TERM: int
    BUY_RATIO: int
    SELL_RATIO: int
    CROSS_TYPE: str

class SwingResponse(SwingCreate):

    class Config:
        orm_mode = True