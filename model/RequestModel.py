from pydantic import BaseModel


# 계좌 번호
# CANO : 앞 8자리
# ACNT_PRDT_CD : 뒤 2자리
class Account(BaseModel):
    CANO: str
    ACNT_PRDT_CD: str