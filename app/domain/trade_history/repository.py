"""
Trade History Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, List
from datetime import datetime
from app.common.database import TradeHistoryModel
import logging

logger = logging.getLogger(__name__)


class TradeHistoryRepository:
    """거래 내역 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def save(self, trade_data: dict) -> TradeHistoryModel:
        """
        거래 내역 저장 (flush만 수행)

        Args:
            trade_data: 거래 정보 딕셔너리

        Returns:
            저장된 TradeHistoryModel
        """
        db_trade = TradeHistoryModel(
            SWING_ID=trade_data["SWING_ID"],
            TRADE_DATE=trade_data.get("TRADE_DATE", datetime.now()),
            TRADE_TYPE=trade_data["TRADE_TYPE"],
            TRADE_PRICE=trade_data["TRADE_PRICE"],
            TRADE_QTY=trade_data["TRADE_QTY"],
            TRADE_AMOUNT=trade_data["TRADE_AMOUNT"],
            TRADE_REASONS=trade_data.get("TRADE_REASONS"),
        )
        self.db.add(db_trade)
        await self.db.flush()
        await self.db.refresh(db_trade)
        return db_trade

    async def find_by_swing_id(self, swing_id: int) -> List[TradeHistoryModel]:
        """
        특정 스윙의 거래 내역 조회

        Args:
            swing_id: 스윙 ID

        Returns:
            거래 내역 리스트 (최신순)
        """
        query = (
            select(TradeHistoryModel)
            .where(TradeHistoryModel.SWING_ID == swing_id)
            .order_by(TradeHistoryModel.TRADE_DATE.desc())
        )
        result = await self.db.execute(query)
        return result.scalars().all()

    async def find_latest_by_type(
        self, swing_id: int, trade_type: str
    ) -> Optional[TradeHistoryModel]:
        """
        특정 스윙의 가장 최근 특정 타입 거래 조회

        Args:
            swing_id: 스윙 ID
            trade_type: 거래 타입 ("B" or "S")

        Returns:
            가장 최근 거래 내역 또는 None
        """
        query = (
            select(TradeHistoryModel)
            .where(
                TradeHistoryModel.SWING_ID == swing_id,
                TradeHistoryModel.TRADE_TYPE == trade_type,
            )
            .order_by(TradeHistoryModel.TRADE_DATE.desc())
            .limit(1)
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def find_by_id(self, trade_id: int) -> Optional[TradeHistoryModel]:
        """
        거래 ID로 조회

        Args:
            trade_id: 거래 ID

        Returns:
            거래 내역 또는 None
        """
        query = select(TradeHistoryModel).where(TradeHistoryModel.TRADE_ID == trade_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()