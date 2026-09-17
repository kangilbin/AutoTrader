"""
Account Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, text
from typing import Optional, List

from app.domain.account.entity import Account
from app.domain.auth.entity import Auth
from app.domain.account.schemas import AccountResponse
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
                Account.ACCOUNT_NO,
                Auth.SIMULATION_YN,
                Auth.API_KEY,
                Auth.SECRET_KEY
            )
            .join(Auth, Account.AUTH_ID == Auth.AUTH_ID)
            .filter(Account.ACCOUNT_ID == account_id)
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

    async def find_auth_id_by_account_no(self, user_id: str, account_no: str) -> Optional[int]:
        """계좌번호로 해당 계좌에 묶인 인증키 ID 조회

        계좌↔인증키 바인딩은 ACCOUNT 행에 영속화되어 있으므로, 계좌번호만 있으면
        실전/모의가 확정된다. 배치처럼 로그인 세션(Redis)이 없는 경로에서
        "어떤 인증키로 주문할지"를 결정하는 근거로 쓴다.

        ⚠️ (USER_ID, ACCOUNT_NO)에 유니크 제약이 없고 계좌 등록도 중복을 막지 않는다.
        앱키 교체 후 같은 계좌가 여러 AUTH_ID로 남을 수 있으므로 최신 등록분
        (ACCOUNT_ID 내림차순)을 쓴다 — 임의 행을 골라 폐기된 키로 주문하는 것을 막는다.
        """
        query = (
            select(Account.AUTH_ID)
            .filter(
                Account.USER_ID == user_id,
                Account.ACCOUNT_NO == account_no,
            )
            .order_by(Account.ACCOUNT_ID.desc())
        )
        result = await self.db.execute(query)
        auth_ids = result.scalars().all()

        if len(auth_ids) > 1:
            logger.warning(
                f"계좌 {account_no}(USER_ID={user_id})에 인증키가 {len(auth_ids)}개 연결됨 "
                f"{auth_ids} → 최신 등록분 AUTH_ID={auth_ids[0]} 사용. 중복 계좌 등록 정리 필요"
            )

        return auth_ids[0] if auth_ids else None

    async def find_account_nos_by_auth(self, user_id: str, auth_id: int) -> List[str]:
        """인증키에 묶인 계좌번호 목록 (인증키 삭제 시 동반 정리 대상)"""
        query = (
            select(Account.ACCOUNT_NO)
            .filter(Account.USER_ID == user_id, Account.AUTH_ID == auth_id)
            .distinct()
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def find_accounts_by_auth(self, user_id: str, auth_id: int) -> List:
        """인증키에 묶인 계좌 행(ACCOUNT_ID, ACCOUNT_NO) 목록"""
        query = select(Account.ACCOUNT_ID, Account.ACCOUNT_NO).filter(
            Account.USER_ID == user_id, Account.AUTH_ID == auth_id
        )
        result = await self.db.execute(query)
        return result.all()

    async def find_surviving_account_nos(
        self, account_nos: List[str], exclude_account_ids: List
    ) -> List[str]:
        """제외 대상 계좌 행을 빼고도 ACCOUNT에 남는 계좌번호

        삭제 '전에' 영향도를 계산할 때 쓴다. 삭제 '후'에 같은 판단을 하는
        find_existing_account_nos와 시점만 다르고 규칙은 같다 — 다른 인증키에도
        묶여 있는 계좌는 삭제 대상에서 제외된다.

        ⚠️ 두 메서드 모두 USER_ID로 범위를 좁히지 않는다. SWING_TRADE에 USER_ID가
        없고 배치가 ACCOUNT_NO만으로 조인하므로(find_active_domestic_swings),
        다른 사용자의 ACCOUNT 행이 남아 있으면 그 스윙은 실제로 계속 동작한다.
        따라서 '남아 있다'로 보고 삭제하지 않는 쪽이 안전하다. 한쪽에만 USER_ID
        조건을 넣으면 미리보기와 실제 삭제가 어긋나므로 반드시 함께 바꿀 것.
        """
        if not account_nos:
            return []
        query = (
            select(Account.ACCOUNT_NO)
            .filter(Account.ACCOUNT_NO.in_(account_nos))
            .distinct()
        )
        if exclude_account_ids:
            query = query.filter(Account.ACCOUNT_ID.notin_(exclude_account_ids))
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def find_existing_account_nos(self, account_nos: List[str]) -> List[str]:
        """주어진 계좌번호 중 ACCOUNT에 아직 남아 있는 것만 반환

        같은 계좌가 여러 인증키로 중복 등록될 수 있으므로(유니크 제약 없음),
        '인증키 하나를 지웠다'와 '그 계좌가 사라졌다'는 다르다. 이 구분이 없으면
        멀쩡한 바인딩이 남은 계좌의 스윙까지 지우게 된다.
        """
        if not account_nos:
            return []
        query = (
            select(Account.ACCOUNT_NO)
            .filter(Account.ACCOUNT_NO.in_(account_nos))
            .distinct()
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def delete_by_auth(self, user_id: str, auth_id: int) -> int:
        """인증키에 묶인 계좌 일괄 삭제 (flush만 수행)"""
        query = delete(Account).filter(
            Account.USER_ID == user_id, Account.AUTH_ID == auth_id
        )
        result = await self.db.execute(query)
        await self.db.flush()
        return result.rowcount

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

    async def save(self, account: Account) -> Account:
        """계좌 저장 (flush만 수행)"""
        self.db.add(account)
        await self.db.flush()
        await self.db.refresh(account)
        return account

    async def update(self, account_id: str, data: dict) -> Optional[Account]:
        """계좌 수정 (flush만 수행)"""
        query = (
            update(Account)
            .filter(Account.ACCOUNT_ID == account_id)
            .values(**data)
            .execution_options(synchronize_session=False)
        )
        await self.db.execute(query)
        await self.db.flush()
        return await self.db.get(Account, account_id)

    async def find_account_no_by_id(self, user_id: str, account_id: str) -> Optional[str]:
        """계좌 ID로 계좌번호 조회 (삭제 전 동반 정리 대상을 확보하는 용도)"""
        query = select(Account.ACCOUNT_NO).filter(
            Account.USER_ID == user_id, Account.ACCOUNT_ID == account_id
        )
        result = await self.db.execute(query)
        return result.scalars().first()

    async def delete(self, user_id: str, account_id: str) -> bool:
        """계좌 삭제 (flush만 수행) - 소유권 검증 포함

        USER_ID 조건이 없으면 ACCOUNT_ID만 아는 사용자가 남의 계좌를 지울 수 있다.
        """
        query = delete(Account).filter(
            Account.USER_ID == user_id, Account.ACCOUNT_ID == account_id
        )
        result = await self.db.execute(query)
        await self.db.flush()
        return result.rowcount > 0