"""
Account Service - 비즈니스 로직 및 트랜잭션 관리
"""
import logging
from datetime import datetime
from typing import List

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt
from app.domain.account.entity import Account
from app.domain.account.repository import AccountRepository
from app.domain.account.schemas import AccountCreateRequest, AccountResponse
from app.domain.auth.repository import AuthRepository
from app.exceptions import NotFoundError, DatabaseError
from app.external.kis_api import issue_token, verify_account_balance

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
            raise DatabaseError("계좌 등록에 실패했습니다", operation="insert", original_error=e)

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
            raise DatabaseError("계좌 수정에 실패했습니다", operation="update", original_error=e)

    async def verify_account(self, user_id: str, auth_id: int, account_no: str) -> dict:
        """계좌번호 검증 - KIS 잔고 조회 API로 유효성 확인"""
        auth_repo = AuthRepository(self.db)
        auth_data = await auth_repo.find_by_id(user_id, auth_id)
        if not auth_data:
            raise NotFoundError("인증키", auth_id)

        access_data = await issue_token(
            auth_data["SIMULATION_YN"],
            decrypt(auth_data["API_KEY"]),
            decrypt(auth_data["SECRET_KEY"]),
        )

        await verify_account_balance(access_data, account_no)
        return {"account_no": account_no, "valid": True}

    async def delete_account(self, account_id: str) -> bool:
        """계좌 삭제"""
        try:
            result = await self.repo.delete(account_id)
            await self.db.commit()
            if not result:
                raise NotFoundError("계좌", account_id)
            return result
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"계좌 삭제 실패: {e}", exc_info=True)
            raise DatabaseError("계좌 삭제에 실패했습니다", operation="delete", original_error=e)