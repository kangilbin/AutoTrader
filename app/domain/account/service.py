"""
Account Service - 비즈니스 로직 및 트랜잭션 관리
"""
import logging
from datetime import datetime
from typing import List

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.account.entity import Account
from app.domain.account.repository import AccountRepository
from app.domain.account.schemas import AccountCreateRequest, AccountResponse
from app.domain.swing.repository import SwingRepository
from app.exceptions import NotFoundError, DatabaseError
from app.external.kis_api import token_for_auth_id, verify_account_balance

logger = logging.getLogger(__name__)


class AccountService:
    """계좌 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AccountRepository(db)
        # 계좌가 사라지면 그 계좌의 스윙·이평선 옵션도 함께 사라져야 한다.
        # 같은 트랜잭션에서 정리하므로 Repository를 직접 쓴다 (Service가 경계).
        self.swing_repo = SwingRepository(db)

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
        """계좌번호 검증 - KIS 잔고 조회 API로 유효성 확인

        토큰은 반드시 캐시 경로(token_for_auth_id)로 받는다. KIS는 동일 appkey로
        1분에 1회만 발급을 허용하므로, 매번 새로 발급하면 인증키 등록 직후
        (등록 과정에서 이미 1회 발급됨) 검증을 누르는 정상 흐름이 403으로 막힌다.
        """
        access_data = await token_for_auth_id(user_id, auth_id, self.db)

        await verify_account_balance(access_data, account_no)
        return {"account_no": account_no, "valid": True}

    async def delete_account(self, user_id: str, account_id: str) -> bool:
        """계좌 삭제 - 스윙·이평선 옵션 동반 삭제, 소유권 검증 포함

        계좌만 지우면 스윙은 배치 조회(JOIN ACCOUNT)에서 빠져 조용히 멈추고,
        같은 계좌번호로 재등록하는 순간 옛 스윙·옛 EMA_OPT가 되살아난다.
        """
        try:
            account_no = await self.repo.find_account_no_by_id(user_id, account_id)
            if not account_no:
                raise NotFoundError("계좌", account_id)

            result = await self.repo.delete(user_id, account_id)
            await self.purge_orphaned_swings([account_no])
            await self.db.commit()
            return result
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"계좌 삭제 실패: {e}", exc_info=True)
            raise DatabaseError("계좌 삭제에 실패했습니다", operation="delete", original_error=e)

    async def delete_accounts_by_auth(self, user_id: str, auth_id: int) -> None:
        """인증키에 묶인 계좌와 그 계좌의 스윙 삭제 (commit은 호출자 책임)

        인증키 삭제 트랜잭션 안에서 호출된다. 삭제 규칙을 계좌 쪽 한 곳에 두어
        '인증키로 지우든 계좌로 지우든 동일하게 정리된다'를 보장한다.
        """
        account_nos = await self.repo.find_account_nos_by_auth(user_id, auth_id)
        await self.repo.delete_by_auth(user_id, auth_id)
        await self.purge_orphaned_swings(account_nos)

    async def purge_orphaned_swings(self, account_nos: List[str]) -> None:
        """ACCOUNT 행이 남지 않은 계좌의 스윙·이평선 옵션 삭제 (commit 없음)

        같은 계좌번호가 다른 인증키로도 등록돼 있으면(중복 등록은 앱키 교체 시
        실제로 발생한다) 배치가 그 바인딩으로 계속 동작하므로 스윙을 남긴다.
        """
        if not account_nos:
            return

        remaining = set(await self.repo.find_existing_account_nos(account_nos))
        orphan_nos = [no for no in account_nos if no not in remaining]
        if not orphan_nos:
            return

        # 보유 포지션이 있는 스윙을 지우면 실제 주식은 증권사에 남은 채 손절·익절
        # 평가만 멈춘다. 삭제는 요청대로 진행하되 추적 가능하도록 경고를 남긴다.
        for swing in await self.swing_repo.find_by_account_nos(orphan_nos):
            if swing.SIGNAL in (1, 2) or (swing.HOLD_QTY or 0) > 0:
                logger.warning(
                    f"[SWING_ID={swing.SWING_ID}] 보유 포지션({swing.HOLD_QTY}주, "
                    f"SIGNAL={swing.SIGNAL}) 상태로 삭제 - 계좌 {swing.ACCOUNT_NO} "
                    f"정리에 따른 동반 삭제. 증권사 잔고 확인 필요"
                )

        deleted = await self.swing_repo.delete_by_account_nos(orphan_nos)
        await self.swing_repo.delete_ema_options_by_account_nos(orphan_nos)
        logger.info(f"계좌 삭제 동반 정리: 계좌 {orphan_nos}, 스윙 {deleted}건")