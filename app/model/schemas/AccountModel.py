from pydantic import BaseModel
from typing import Optional
from datetime import datetime


# CANO : 앞 8자리
# ACNT_PRDT_CD : 뒤 2자리
class AccountCreate(BaseModel):
    ACCOUNT_ID: int
    USER_ID: Optional[str] = None
    ACCOUNT_NO: Optional[str] = None
    AUTH_ID: Optional[int] = None
    REG_DT: Optional[datetime] = None
    MOD_DT: Optional[datetime] = None

class AccountResponse(AccountCreate):
    class Config:
        orm_mode = True