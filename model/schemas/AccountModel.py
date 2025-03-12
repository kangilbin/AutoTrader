from pydantic import BaseModel


# CANO : 앞 8자리
# ACNT_PRDT_CD : 뒤 2자리
class AccountCreate(BaseModel):
    USER_ID: str
    CANO: str
    ACNT_PRDT_CD: str


class AccountResponse(AccountCreate):
    class Config:
        orm_mode = True