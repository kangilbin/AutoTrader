"""
Trade History Service - 비즈니스 로직 및 트랜잭션 관리
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from typing import List, Optional
from datetime import datetime
from decimal import Decimal
import json
import logging

from app.domain.trade_history.repository import TradeHistoryRepository
from app.domain.trade_history.schemas import TradeHistoryResponse
from app.exceptions import DatabaseError, NotFoundError

logger = logging.getLogger(__name__)


class TradeHistoryService:
    """거래 내역 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = TradeHistoryRepository(db)

    async def record_trade(
        self,
        swing_id: int,
        trade_type: str,
        order_result: dict,
        reasons: Optional[list[str]] = None,
    ) -> dict:
        """
        거래 내역 저장 (공통 로직)

        Args:
            swing_id: 스윙 ID
            trade_type: 거래 타입 ("B": 매수, "S": 매도)
            order_result: 주문 실행 결과
                - avg_price: 평균 체결가
                - qty: 체결 수량
                - amount: 거래 금액
            reasons: 매매 사유 리스트 (선택)

        Returns:
            저장된 거래 내역

        Raises:
            DatabaseError: 저장 실패 시
        """
        try:
            # 매매 사유 JSON 변환
            trade_reasons = json.dumps(reasons, ensure_ascii=False) if reasons else None

            # 거래 데이터 준비
            trade_data = {
                "SWING_ID": swing_id,
                "TRADE_DATE": datetime.now(),
                "TRADE_TYPE": trade_type,
                "TRADE_PRICE": Decimal(str(order_result.get("avg_price", 0))),
                "TRADE_QTY": order_result.get("qty", 0),
                "TRADE_AMOUNT": Decimal(str(order_result.get("amount", 0))),
                "TRADE_REASONS": trade_reasons,
            }

            # Repository를 통해 저장 (flush만)
            db_trade = await self.repo.save(trade_data)

            # commit은 호출자(전략)에서 수행
            logger.info(
                f"[SWING {swing_id}] {trade_type} 거래 내역 저장: "
                f"가격={trade_data['TRADE_PRICE']:,}, 수량={trade_data['TRADE_QTY']}"
            )

            return TradeHistoryResponse.model_validate(db_trade).model_dump()

        except SQLAlchemyError as e:
            logger.error(f"거래 내역 저장 실패: {e}", exc_info=True)
            raise DatabaseError("거래 내역 저장에 실패했습니다")

    async def get_trade_history(self, swing_id: int) -> List[dict]:
        """
        특정 스윙의 거래 내역 조회

        Args:
            swing_id: 스윙 ID

        Returns:
            거래 내역 리스트
        """
        try:
            trades = await self.repo.find_by_swing_id(swing_id)
            return [
                TradeHistoryResponse.model_validate(trade).model_dump()
                for trade in trades
            ]
        except SQLAlchemyError as e:
            logger.error(f"거래 내역 조회 실패: {e}", exc_info=True)
            raise DatabaseError("거래 내역 조회에 실패했습니다")

    async def get_latest_buy(self, swing_id: int) -> Optional[dict]:
        """
        가장 최근 매수 내역 조회

        Args:
            swing_id: 스윙 ID

        Returns:
            최근 매수 내역 또는 None
        """
        try:
            trade = await self.repo.find_latest_by_type(swing_id, "B")
            if trade:
                return TradeHistoryResponse.model_validate(trade).model_dump()
            return None
        except SQLAlchemyError as e:
            logger.error(f"최근 매수 내역 조회 실패: {e}", exc_info=True)
            raise DatabaseError("최근 매수 내역 조회에 실패했습니다")

    async def get_latest_sell(self, swing_id: int) -> Optional[dict]:
        """
        가장 최근 매도 내역 조회

        Args:
            swing_id: 스윙 ID

        Returns:
            최근 매도 내역 또는 None
        """
        try:
            trade = await self.repo.find_latest_by_type(swing_id, "S")
            if trade:
                return TradeHistoryResponse.model_validate(trade).model_dump()
            return None
        except SQLAlchemyError as e:
            logger.error(f"최근 매도 내역 조회 실패: {e}", exc_info=True)
            raise DatabaseError("최근 매도 내역 조회에 실패했습니다")