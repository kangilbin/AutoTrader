from pydantic import BaseModel


# CANO : 앞 8자리
# ACNT_PRDT_CD : 뒤 2자리
class StockCreate(BaseModel):
    ST_CODE: str
    SD_CODE: str
    NAME: str
    DATA_YN: str
    DEL_YN: str
    REG_DT: str


class StockResponse(StockCreate):

    class Config:
        orm_mode = True