from model import AccountModel
from module.DBConnection import DBConnectionPool, sql_execute
from datetime import datetime



# 계좌 테이블 #

# 계좌 정보 조회
async def get_account_info(pool: DBConnectionPool, id: str):
    query = "SELECT * FROM ACCOUNT where ID = %s"
    return await sql_execute(pool, query, (id,))


# 계좌 등록
async def account_register(pool: DBConnectionPool, account: AccountModel):
    query = "INSERT INTO ACCOUNT (USER_ID, CANO, ACNT_PRDT_CD, REG_DT) VALUES (%s, %s, %s, %s)"
    return await sql_execute(pool, query, (account.USER_ID, account.CANO, account.ACNT_PRDT_CD, datetime.now()))
