"""
Pool Service - 풀 설정 비즈니스 로직 및 트랜잭션 관리
"""
import logging
from typing import List

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.account.repository import AccountRepository
from app.domain.pool.entity import PoolSetting, VALID_POOL_MARKETS
from app.domain.pool.repository import PoolRepository
from app.domain.pool.schemas import PoolSettingResponse, PoolSettingUpdateRequest
from app.domain.swing.trading import parking
from app.common.redis import get_redis
from app.core.market_code import is_overseas
from app.exceptions import BusinessRuleError, DatabaseError, DuplicateError, ExternalServiceError, NotFoundError

logger = logging.getLogger(__name__)

_UPDATABLE = ("USE_YN", "MAX_POSITIONS", "PARK_RESERVE_PCT")   # 비중·리스크는 종목 수에서 자동 계산(entity)


class PoolService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = PoolRepository(db)
        self.account_repo = AccountRepository(db)

    async def _verify_account(self, user_id: str, account_no: str) -> None:
        """사용자의 계좌인지 확인 — 남의 계좌는 '없음'으로 응답해 존재 여부를 드러내지 않는다"""
        if await self.account_repo.find_auth_id_by_account_no(user_id, account_no) is None:
            raise NotFoundError("계좌", account_no)

    async def get_settings(self, user_id: str, account_no: str) -> List[dict]:
        """계좌의 풀 설정 (국내·미국) — 저장된 행이 없으면 기본값(꺼짐)을 보여주되 DB에는 만들지 않는다"""
        await self._verify_account(user_id, account_no)
        saved = {s.MARKET: s for s in await self.repo.find_by_account(account_no)}
        return [
            PoolSettingResponse.model_validate(saved.get(m) or PoolSetting.create(account_no, m)).model_dump()
            for m in VALID_POOL_MARKETS
        ]

    async def update_setting(self, user_id: str, request: PoolSettingUpdateRequest) -> dict:
        """풀 설정 저장 — 보낸 항목만 반영, 행이 없으면 기본값으로 만든 뒤 반영

        MAX_POSITIONS 는 None(제한 없음)이 유효한 값이라 '보내지 않음'과 '명시적 null'을 구분한다.
        """
        await self._verify_account(user_id, request.ACCOUNT_NO)
        changes = {k: getattr(request, k) for k in _UPDATABLE if k in request.model_fields_set}
        try:
            setting = await self.repo.find(request.ACCOUNT_NO, request.MARKET)
            if setting is None:
                # 새 행은 켜진 상태로 만든다 — 파킹 설정만 저장해도 이 계좌의 스윙이 매수에서 빠지지 않도록
                # (풀 스위치는 앱에서 없앴다. 꺼짐은 행이 없을 때뿐이며 스윙 등록·활성화가 행을 보장한다)
                setting = PoolSetting.create(request.ACCOUNT_NO, request.MARKET, use_yn='Y')
            setting.update_settings(**changes)
            await self.repo.save(setting)
            await self.db.commit()
            return PoolSettingResponse.model_validate(setting).model_dump()
        except IntegrityError:
            await self.db.rollback()
            raise DuplicateError("풀 설정", f"{request.ACCOUNT_NO}/{request.MARKET}")
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"풀 설정 저장 실패: {e}", exc_info=True)
            raise DatabaseError("풀 설정 저장에 실패했습니다")
        except Exception:
            await self.db.rollback()
            raise

    async def _parking_setting(self, user_id: str, account_no: str, market: str) -> PoolSetting:
        """파킹을 쓸 수 있는 풀 설정 — 계좌 소유 확인 + 풀 켜짐 확인"""
        await self._verify_account(user_id, account_no)
        setting = await self.repo.find(account_no, market)
        if setting is None or not setting.parking_enabled():
            raise BusinessRuleError("풀 설정을 저장한 뒤 사용할 수 있습니다", rule="PARKING_NOT_ENABLED")
        return setting

    async def get_parking(self, user_id: str, account_no: str, market: str) -> dict:
        """파킹 ETF 보유 현황 (수량·평가금) — 보유를 읽지 못하면 QTY/VALUE 는 None"""
        setting = await self._parking_setting(user_id, account_no, market)
        holding = await parking.fetch_park_holding(user_id, self.db, account_no, setting)
        overseas = is_overseas(setting.park_mrkt_code)
        value = None if holding is None else holding[0] * holding[1]
        return {
            "PARK_CODE": setting.park_code,
            "QTY": None if holding is None else holding[0],
            "VALUE": None if value is None else (round(value, 2) if overseas else int(value)),
        }

    async def park(self, user_id: str, account_no: str, market: str) -> dict:
        """유휴 현금을 파킹 ETF로 전환 — 결과 상태(parked/nothing/unconfirmed/failed/busy)와 메시지를 돌려준다"""
        setting = await self._parking_setting(user_id, account_no, market)
        try:
            redis = await get_redis()
        except Exception as e:
            logger.error(f"파킹 락용 Redis 연결 실패: {e}")
            raise ExternalServiceError("Redis", "파킹 처리를 시작할 수 없습니다")
        result = await parking.park_idle_cash(user_id, self.db, redis, setting)
        overseas = is_overseas(setting.park_mrkt_code)
        return {
            "STATUS": result.status,
            "QTY": result.qty,
            "AMOUNT": round(result.amount, 2) if overseas else int(result.amount),
            "MESSAGE": result.message,
        }
