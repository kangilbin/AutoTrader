import json
from crud.AccountCrud import insert_account, select_account, delete_account, list_account
from model.schemas.AccountModel import AccountCreate, AccountResponse
from sqlalchemy.ext.asyncio import AsyncSession
from module.RedisConnection import redis

# 계좌 등록
async def create_account(db: AsyncSession, account_data: AccountCreate) -> AccountResponse:
    return await insert_account(db, account_data)


# 계좌 조회
async def get_account(db: AsyncSession, account_id: str, user_id: str):
    account_info = await select_account(db, account_id)
    account_info_json = json.loads(account_info)
    await redis().hset(user_id, "CANO", account_info_json.get("CANO"))
    await redis().hset(user_id, "ACNT_PRDT_CD", account_info_json.get("ACNT_PRDT_CD"))

    return account_info_json


# 계좌 삭제
async def remove_account(db: AsyncSession, account_id: str):
    await delete_account(db, account_id)


async def get_accounts(db: AsyncSession, user_id: str):
    return await list_account(db, user_id)


