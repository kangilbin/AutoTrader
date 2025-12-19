"""
Swing Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, text, and_
from typing import Optional, List

from app.common.database import SwingModel, EmaOptModel
from app.domain.swing.entity import SwingTrade, EmaOption
import logging

logger = logging.getLogger(__name__)


class SwingRepository:
    """스윙 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_by_id(self, swing_id: int) -> Optional[SwingModel]:
        """스윙 조회"""
        query = select(SwingModel).filter(SwingModel.SWING_ID == swing_id)
        result = await self.db.execute(query)
        return result.scalars().first()

    async def find_by_account(self, user_id: str, account_no: str) -> List[SwingModel]:
        """계좌별 스윙 조회"""
        query = select(SwingModel).filter(
            and_(SwingModel.USER_ID == user_id, SwingModel.ACCOUNT_NO == account_no)
        )
        result = await self.db.execute(query)
        return result.scalars().all()

    async def find_all_by_account_no(self, account_no: str) -> List[SwingModel]:
        """계좌번호로 스윙 목록 조회"""
        query = select(SwingModel).filter(SwingModel.ACCOUNT_NO == account_no)
        result = await self.db.execute(query)
        return result.scalars().all()

    async def find_active_swings(self) -> List:
        """활성화된 스윙 목록 조회 (배치용)"""
        query = text(
            "SELECT ST.*, U.API_KEY, U.SECRET_KEY "
            "FROM SWING_TRADE ST "
            "LEFT JOIN ACCOUNT A ON ST.ACCOUNT_NO = A.ACCOUNT_NO "
            "LEFT JOIN USER U ON A.USER_ID = U.USER_ID "
            "WHERE ST.USE_YN = 'Y'"
        )
        result = await self.db.execute(query)
        return result.all()

    async def save(self, swing: SwingTrade) -> SwingModel:
        """스윙 저장 (flush만 수행)"""
        db_swing = SwingModel(
            ACCOUNT_NO=swing.account_no,
            ST_CODE=swing.st_code,
            INIT_AMOUNT=swing.init_amount,
            CUR_AMOUNT=swing.cur_amount,
            SWING_TYPE=swing.swing_type,
            BUY_RATIO=swing.buy_ratio,
            SELL_RATIO=swing.sell_ratio,
            SIGNAL=swing.signal
        )
        self.db.add(db_swing)
        await self.db.flush()
        await self.db.refresh(db_swing)
        return db_swing

    async def save_ema_option(self, ema: EmaOption) -> EmaOptModel:
        """이평선 옵션 저장 (flush만 수행)"""
        db_ema = EmaOptModel(
            ACCOUNT_NO=ema.account_no,
            ST_CODE=ema.st_code,
            SHORT_TERM=ema.short_term,
            MEDIUM_TERM=ema.medium_term,
            LONG_TERM=ema.long_term
        )
        self.db.add(db_ema)
        await self.db.flush()
        await self.db.refresh(db_ema)
        return db_ema

    async def update(self, swing_id: int, data: dict) -> Optional[SwingModel]:
        """스윙 수정 (flush만 수행)"""
        query = (
            update(SwingModel)
            .filter(SwingModel.SWING_ID == swing_id)
            .values(**data)
            .execution_options(synchronize_session=False)
        )
        await self.db.execute(query)
        await self.db.flush()
        return await self.db.get(SwingModel, swing_id)

    async def delete(self, swing_id: int) -> bool:
        """스윙 삭제 (flush만 수행)"""
        query = delete(SwingModel).filter(SwingModel.SWING_ID == swing_id)
        result = await self.db.execute(query)
        await self.db.flush()
        return result.rowcount > 0

    async def reset_signals_by_value(self, old_value: int, new_value: int) -> int:
        """
        특정 신호 값을 일괄 변경 (flush만 수행)

        Args:
            old_value: 기존 신호 값
            new_value: 새로운 신호 값

        Returns:
            업데이트된 행 개수
        """
        from datetime import datetime
        query = (
            update(SwingModel)
            .filter(SwingModel.SIGNAL == old_value)
            .values(SIGNAL=new_value, MOD_DT=datetime.now())
            .execution_options(synchronize_session=False)
        )
        result = await self.db.execute(query)
        await self.db.flush()
        return result.rowcount