"""
Account Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, text
from typing import Optional, List

from app.common.database import AccountModel, AuthModel
from app.account.entity import Account
from app.account.schemas import AccountResponse
import logging

logger = logging.getLogger(__name__)


class AccountRepository:
    """계좌 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_by_id(self, account_id: str) -> Optional[dict]:
        """계좌 상세 조회 (인증키 정보 포함)"""
        query = (
            select(
                AccountModel.ACCOUNT_NO,
                AuthModel.SIMULATION_YN,
                AuthModel.API_KEY,
                AuthModel.SECRET_KEY
            )
            .join(AuthModel, AccountModel.AUTH_ID == AuthModel.AUTH_ID)
            .filter(AccountModel.ACCOUNT_ID == account_id)
        )
        result = await self.db.execute(query)
        row = result.first()

        if not row:
            return None

        return {
            "ACCOUNT_NO": row.ACCOUNT_NO,
            "SIMULATION_YN": row.SIMULATION_YN,
            "API_KEY": row.API_KEY,
            "SECRET_KEY": row.SECRET_KEY
        }

    async def find_all_by_user(self, user_id: str) -> List[dict]:
        """사용자의 모든 계좌 조회"""
        query = text(
            "SELECT AT.ACCOUNT_ID, AT.ACCOUNT_NO, AT.AUTH_ID, AK.SIMULATION_YN "
            "FROM ACCOUNT AT "
            "LEFT JOIN AUTH_KEY AK ON AT.AUTH_ID = AK.AUTH_ID "
            "WHERE AT.USER_ID = :user_id"
        )
        result = await self.db.execute(query, {"user_id": user_id})
        return [AccountResponse.model_validate(row).model_dump() for row in result]

    async def save(self, account: Account) -> AccountModel:
        """계좌 저장 (flush만 수행)"""
        db_account = AccountModel(
            USER_ID=account.user_id,
            ACCOUNT_NO=account.account_no,
            AUTH_ID=account.auth_id
        )
        self.db.add(db_account)
        await self.db.flush()
        await self.db.refresh(db_account)
        return db_account

    async def update(self, account_id: str, data: dict) -> Optional[AccountModel]:
        """계좌 수정 (flush만 수행)"""
        query = (
            update(AccountModel)
            .filter(AccountModel.ACCOUNT_ID == account_id)
            .values(**data)
            .execution_options(synchronize_session=False)
        )
        await self.db.execute(query)
        await self.db.flush()
        return await self.db.get(AccountModel, account_id)

    async def delete(self, account_id: str) -> bool:
        """계좌 삭제 (flush만 수행)"""
        query = delete(AccountModel).filter(AccountModel.ACCOUNT_ID == account_id)
        result = await self.db.execute(query)
        await self.db.flush()
        return result.rowcount > 0