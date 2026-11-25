"""
Trade History Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case
from typing import Optional, List, Tuple
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
            TOTAL_FEE=trade_data.get("TOTAL_FEE"),
            REALIZED_PNL=trade_data.get("REALIZED_PNL"),
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

    async def count_by_swing_id(self, swing_id: int) -> dict:
        """
        스윙별 거래 통계 (총 건수, 매수/매도 건수)

        Returns:
            {"total_count": int, "buy_count": int, "sell_count": int}
        """
        query = select(
            func.count().label("total_count"),
            func.sum(case((TradeHistory.TRADE_TYPE == "B", 1), else_=0)).label("buy_count"),
            func.sum(case((TradeHistory.TRADE_TYPE == "S", 1), else_=0)).label("sell_count"),
        ).where(TradeHistory.SWING_ID == swing_id)

        result = await self.db.execute(query)
        row = result.one()
        return {
            "total_count": row.total_count or 0,
            "buy_count": row.buy_count or 0,
            "sell_count": row.sell_count or 0,
        }

    async def find_by_swing_id_paged(
        self, swing_id: int, page: int, size: int
    ) -> Tuple[List[TradeHistory], int]:
        """
        페이징 거래 내역 조회

        Returns:
            (거래 내역 리스트, 전체 건수)
        """
        # 전체 건수
        count_query = select(func.count()).where(TradeHistory.SWING_ID == swing_id)
        count_result = await self.db.execute(count_query)
        total_count = count_result.scalar() or 0

        # 페이징 데이터
        offset = (page - 1) * size
        data_query = (
            select(TradeHistory)
            .where(TradeHistory.SWING_ID == swing_id)
            .order_by(TradeHistory.TRADE_DATE.desc())
            .offset(offset)
            .limit(size)
        )
        data_result = await self.db.execute(data_query)
        trades = data_result.scalars().all()

        return trades, total_count

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
