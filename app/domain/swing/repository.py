"""
Swing Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, text, and_
from typing import Optional, List
from app.domain.swing.entity import SwingTrade, EmaOption
from app.domain.stock.entity import Stock
from app.domain.swing.schemas import SwingResponse
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class SwingRepository:
    """스윙 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_by_id(self, swing_id: int) -> Optional[SwingTrade]:
        """스윙 조회"""
        query = select(SwingTrade).filter(SwingTrade.SWING_ID == swing_id)
        result = await self.db.execute(query)
        return result.scalars().first()

    async def find_all_by_account_no(self, account_no: str) -> List[dict]:
        """계좌번호로 스윙 목록 조회"""
        query = (
            select(*SwingTrade.__table__.columns, Stock.ST_NM)
            .join(
                Stock,
                and_(
                    SwingTrade.MRKT_CODE == Stock.MRKT_CODE,
                    SwingTrade.ST_CODE == Stock.ST_CODE
                ),
                isouter=True
            )
            .filter(SwingTrade.ACCOUNT_NO == account_no)
        )
        result = await self.db.execute(query)
        return [SwingResponse(**row).model_dump() for row in result.mappings().all()]


    async def find_active_swings(self) -> List:
        """활성화된 스윙 목록 조회 (배치용)"""
        query = text(
            "SELECT ST.*, A.USER_ID, U.API_KEY, U.SECRET_KEY "
            "FROM SWING_TRADE ST "
            "LEFT JOIN ACCOUNT A ON ST.ACCOUNT_NO = A.ACCOUNT_NO "
            "LEFT JOIN AUTH_KEY U ON A.USER_ID = U.USER_ID AND A.AUTH_ID = U.AUTH_ID "
            "WHERE ST.USE_YN = 'Y'"
        )
        result = await self.db.execute(query)
        return result.all()

    async def save(self, swing: SwingTrade) -> SwingTrade:
        """스윙 저장 (flush만 수행)"""
        self.db.add(swing)
        await self.db.flush()
        await self.db.refresh(swing)
        return swing

    async def save_ema_option(self, ema: EmaOption) -> EmaOption:
        """이평선 옵션 저장 (flush만 수행)"""
        self.db.add(ema)
        await self.db.flush()
        await self.db.refresh(ema)
        return ema

    async def delete_ema_option(self, swing_id: int) -> bool:
        """스이평선 삭제 (flush만 수행)"""
        query = delete(EmaOption).filter(EmaOption.SWING_ID == swing_id)
        result = await self.db.execute(query)
        await self.db.flush()
        return result.rowcount > 0


    async def update(self, swing_id: int, data: dict) -> Optional[SwingTrade]:
        """스윙 수정 (flush만 수행)"""
        query = (
            update(SwingTrade)
            .filter(SwingTrade.SWING_ID == swing_id)
            .values(**data)
            .execution_options(synchronize_session=False)
        )
        await self.db.execute(query)
        await self.db.flush()
        return await self.db.get(SwingTrade, swing_id)

    async def delete(self, swing_id: int) -> bool:
        """스윙 삭제 (flush만 수행)"""
        query = delete(SwingTrade).filter(SwingTrade.SWING_ID == swing_id)
        result = await self.db.execute(query)
        await self.db.flush()
        return result.rowcount > 0

    async def find_active_stock_codes(self) -> List[tuple]:
        """
        활성화된 종목 코드 목록 조회 (EMA 캐시 워밍업용)

        Returns:
            USE_YN='Y'인 고유 (MRKT_CODE, ST_CODE) 튜플 리스트
        """
        query = (
            select(SwingTrade.MRKT_CODE, SwingTrade.ST_CODE)
            .filter(SwingTrade.USE_YN == 'Y')
            .distinct()
        )
        result = await self.db.execute(query)
        return result.all()


