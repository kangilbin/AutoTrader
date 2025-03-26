from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete

from model.TableCreate import Account
from model.schemas.AccountModel import AccountCreate
import logging
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


# 계좌 조회
async def select_account(db: AsyncSession, account_id: str):
    try:
        query = select(Account).filter(Account.ACCOUNT_ID == account_id)
        result = await db.execute(query)
    except SQLAlchemyError as e:
        logger.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return result.scalars()


# 계좌 목록
async def list_account(db: AsyncSession, user_id: str, auth_id: int):
    try:
        query = select(Account).filter(Account.USER_ID == user_id and Account.AUTH_ID == auth_id)
        result = await db.execute(query)
    except SQLAlchemyError as e:
        logger.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return result.scalars().all()


# 계좌 생성
async def insert_account(db: AsyncSession, account_data: AccountCreate):
    try:
        db_account = Account(USER_ID=account_data.USER_ID, ACCOUNT_NO=account_data.ACCOUNT_NO, AUTH_ID=account_data.AUTH_ID)
        db.add(db_account)
        await db.commit()
        await db.refresh(db_account)
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return db_account


# 계좌 업데이트
async def update_account(db: AsyncSession, account_data: AccountCreate, account_id: str):
    try:
        query = (
            update(Account)
            .filter(Account.ACCOUNT_ID == account_id)
            .values(**account_data.dict())
            .execution_options(synchronize_session=False)
        )
        await db.execute(query)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return await db.get(Account, account_id)


# 계좌 삭제
async def delete_account(db: AsyncSession, account_id: str):
    try:
        query = (
            delete(Account)
            .filter(Account.ACCOUNT_ID == account_id)
        )
        result = await db.execute(query)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error occurred: {e}", exc_info=True)
        raise
    if result.rowcount == 0:
        return None  # 삭제된 행이 없으면 None 반환
    return account_id
