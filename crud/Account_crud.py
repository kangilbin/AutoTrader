from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from model.schemas import AccountModel
from model.orm.Account import Account


# 계좌 조회
async def get_account(db: AsyncSession, account_id: str):
    query = select(Account).filter(Account.ACCOUNT_ID.is_(account_id) if account_id is None else Account.ACCOUNT_ID == account_id)
    result = await db.execute(query)
    return result.scalars()


# 계좌 생성
async def create_account(db: AsyncSession, account_data: AccountModel):
    db_account = Account(USER_ID=account_data.USER_ID, CANO=account_data.CANO, ACNT_PRDT_CD=account_data.ACNT_PRDT_CD)
    db.add(db_account)  
    await db.commit() 
    await db.refresh(db_account) 
    return db_account


# 계좌 업데이트
async def update_account(db: AsyncSession, account_data: AccountModel, account_id: str):
    query = (
        update(Account)
        .where(Account.ACCOUNT_ID.is_(account_id) if account_id is None else Account.ACCOUNT_ID == account_id)
        .values(**account_data.dict())
        .execution_options(synchronize_session=False)
    )
    await db.execute(query)
    await db.commit()
    return await db.get(Account, account_id)


# 계좌 삭제
async def delete_account(db: AsyncSession, account_id: str):
    query = (
        delete(Account)
        .where(Account.ACCOUNT_ID.is_(account_id) if account_id is None else Account.ACCOUNT_ID == account_id)
    )
    result = await db.execute(query)
    await db.commit()

    if result.rowcount == 0:
        return None  # 삭제된 행이 없으면 None 반환
    return account_id
