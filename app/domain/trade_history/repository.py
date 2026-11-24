"""
Trade History Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, List
from datetime import datetime
from app.domain.trade_history.entity import TradeHistory
from app.domain.swing.entity import SwingTrade
from app.domain.account.entity import Account
import logging

logger = logging.getLogger(__name__)


class TradeHistoryRepository:
    """거래 내역 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def save(self, trade_data: dict) -> TradeHistory:
        """
        거래 내역 저장 (flush만 수행)

        Args:
            trade_data: 거래 정보 딕셔너리

        Returns:
            저장된 TradeHistory
        """
        db_trade = TradeHistory(
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

    async def find_by_swing_id(self, swing_id: int) -> List[TradeHistory]:
        """
        특정 스윙의 거래 내역 조회

        Args:
            swing_id: 스윙 ID

        Returns:
            거래 내역 리스트 (최신순)
        """
        query = (
            select(TradeHistory)
            .where(TradeHistory.SWING_ID == swing_id)
            .order_by(TradeHistory.TRADE_DATE.desc())
        )
        result = await self.db.execute(query)
        return result.scalars().all()

    async def find_by_swing_id_and_period(
        self, swing_id: int, start_date: datetime, end_date: datetime
    ) -> List[TradeHistory]:
        """
        특정 스윙의 기간별 거래 내역 조회

        Args:
            swing_id: 스윙 ID
            start_date: 조회 시작일
            end_date: 조회 종료일

        Returns:
            거래 내역 리스트 (날짜 오름차순)
        """
        query = (
            select(TradeHistory)
            .where(
                TradeHistory.SWING_ID == swing_id,
                TradeHistory.TRADE_DATE >= start_date,
                TradeHistory.TRADE_DATE <= end_date,
            )
            .order_by(TradeHistory.TRADE_DATE.asc())
        )
        result = await self.db.execute(query)
        return result.scalars().all()

    async def find_swing_with_ownership(
        self, swing_id: int, user_id: str
    ) -> Optional[SwingTrade]:
        """
        스윙 조회 + 소유권 검증

        SWING_TRADE → ACCOUNT 조인으로 사용자 소유 여부 확인

        Args:
            swing_id: 스윙 ID
            user_id: 사용자 ID

        Returns:
            소유권 검증된 SwingTrade 또는 None
        """
        query = (
            select(SwingTrade)
            .join(Account, SwingTrade.ACCOUNT_NO == Account.ACCOUNT_NO)
            .where(
                SwingTrade.SWING_ID == swing_id,
                Account.USER_ID == user_id,
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
