"""
Account Service - 비즈니스 로직 및 트랜잭션 관리
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime
from typing import List
import logging

from app.domain.account.repository import AccountRepository
from app.domain.account.entity import Account
from app.domain.account.schemas import AccountCreateRequest, AccountResponse
from app.exceptions.http import BusinessException, NotFoundException
from app.module.redis_connection import get_redis

logger = logging.getLogger(__name__)


class AccountService:
    """계좌 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AccountRepository(db)

    async def create_account(self, user_id: str, request: AccountCreateRequest) -> dict:
        """계좌 등록"""
        try:
            # 도메인 엔티티 생성 (비즈니스 검증)
            account = Account.create(
                user_id=user_id,
                account_no=request.ACCOUNT_NO,
                auth_id=request.AUTH_ID
            )

            db_account = await self.repo.save(account)
            await self.db.commit()

            return AccountResponse.model_validate(db_account).model_dump()
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"계좌 등록 실패: {e}", exc_info=True)
            raise BusinessException("계좌 등록에 실패했습니다")

    async def get_account(self, account_id: str, user_id: str) -> dict:
        """계좌 조회 및 Redis 캐싱"""
        account_info = await self.repo.find_by_id(account_id)

        if not account_info:
            raise NotFoundException("계좌", account_id)

        # Redis에 캐싱
        redis = await get_redis()
        await redis.hset(user_id, mapping=account_info)

        return account_info

    async def get_accounts(self, user_id: str) -> List[dict]:
        """계좌 목록 조회"""
        return await self.repo.find_all_by_user(user_id)

    async def update_account(self, account_id: str, data: dict) -> dict:
        """계좌 수정"""
        try:
            data["MOD_DT"] = datetime.now()
            result = await self.repo.update(account_id, data)
            await self.db.commit()
            return AccountResponse.model_validate(result).model_dump()
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"계좌 수정 실패: {e}", exc_info=True)
            raise BusinessException("계좌 수정에 실패했습니다")

    async def delete_account(self, account_id: str) -> bool:
        """계좌 삭제"""
        try:
            result = await self.repo.delete(account_id)
            await self.db.commit()
            if not result:
                raise NotFoundException("계좌", account_id)
            return result
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"계좌 삭제 실패: {e}", exc_info=True)
            raise BusinessException("계좌 삭제에 실패했습니다")