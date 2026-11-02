"""
Pool Repository - 데이터 접근 계층 (flush만, commit 안 함)
"""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.pool.entity import PoolSetting


class PoolRepository:
    """풀 설정 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find(self, account_no: str, market: str) -> Optional[PoolSetting]:
        """계좌·시장의 풀 설정 조회"""
        result = await self.db.execute(
            select(PoolSetting).filter(PoolSetting.ACCOUNT_NO == account_no, PoolSetting.MARKET == market)
        )
        return result.scalars().first()

    async def find_by_account(self, account_no: str) -> List[PoolSetting]:
        """계좌의 풀 설정 전체 (국내·미국)"""
        result = await self.db.execute(select(PoolSetting).filter(PoolSetting.ACCOUNT_NO == account_no))
        return list(result.scalars().all())

    async def find_all_active(self) -> List[PoolSetting]:
        """풀 매수가 켜진 설정 전체 — 배치가 어떤 계좌·시장을 풀 모드로 돌릴지 정하는 근거"""
        result = await self.db.execute(select(PoolSetting).filter(PoolSetting.USE_YN == 'Y'))
        return list(result.scalars().all())

    async def ensure_active(self, account_no: str, market: str) -> PoolSetting:
        """이 계좌·시장의 풀을 켜진 상태로 보장한다 (flush만, commit 안 함)

        배정금이 없어 풀이 유일한 매수 방식이므로, 스윙을 등록·활성화하면 풀도 켜져야 매수가 일어난다.
        사용자가 종목마다 따로 켤 스위치는 앱에서 없앴고, 이 보장이 그 역할을 한다.
        행이 없으면 만들고, 꺼져 있으면 켠다.
        """
        setting = await self.find(account_no, market)
        if setting is None:
            created = await self._insert_new(PoolSetting.create(account_no, market, use_yn='Y'))
            if created is not None:
                return created
            # 같은 계좌·시장의 첫 등록·활성화가 동시에 들어와 다른 요청이 먼저 행을 만들었다 — 그 행을 쓴다.
            # (같은 트랜잭션에서는 방금 커밋된 행이 보이지 않을 수 있어 못 찾으면 켜진 상태의 임시 객체를 돌려준다:
            #  그 행은 이미 존재하고, 새 행은 항상 켜진 상태로 만들어진다)
            setting = await self.find(account_no, market) or PoolSetting.create(account_no, market, use_yn='Y')
        if not setting.is_active():
            setting.update_settings(USE_YN='Y')
            await self.save(setting)
        return setting

    async def _insert_new(self, setting: PoolSetting) -> Optional[PoolSetting]:
        """새 행 삽입 — 기본키 충돌(동시 생성)이면 None. savepoint 안에서 해 호출한 쪽의 트랜잭션을 깨지 않는다
        (충돌이 바깥 IntegrityError 로 번지면 스윙 등록이 '중복'으로 거절된다)"""
        try:
            async with self.db.begin_nested():
                self.db.add(setting)
                await self.db.flush()
            return setting
        except IntegrityError:
            return None

    async def save(self, setting: PoolSetting) -> PoolSetting:
        """저장 (flush만, commit 안 함)"""
        self.db.add(setting)
        await self.db.flush()
        return setting
