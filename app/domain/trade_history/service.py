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

import pandas as pd
import talib as ta
from dateutil.relativedelta import relativedelta

from app.domain.trade_history.repository import TradeHistoryRepository
from app.domain.trade_history.schemas import TradeHistoryResponse
from app.exceptions import DatabaseError, NotFoundError, PermissionDeniedError

logger = logging.getLogger(__name__)


class TradeHistoryService:
    """거래 내역 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = TradeHistoryRepository(db)

    # 거래 비용률 (백테스트와 동일)
    COMMISSION_RATE = Decimal("0.00147")  # 증권사 수수료율
    TAX_RATE = Decimal("0.0020")  # 거래세율

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

            sell_price = Decimal(str(order_result.get("avg_price", 0)))
            sell_qty = order_result.get("qty", 0)
            sell_amount = Decimal(str(order_result.get("amount", 0)))

            # 거래 데이터 준비
            trade_data = {
                "SWING_ID": swing_id,
                "TRADE_DATE": datetime.now(),
                "TRADE_TYPE": trade_type,
                "TRADE_PRICE": sell_price,
                "TRADE_QTY": sell_qty,
                "TRADE_AMOUNT": sell_amount,
                "TRADE_REASONS": trade_reasons,
            }

            # 매도 시 실현손익 계산
            if trade_type == "S" and sell_qty > 0:
                trade_data.update(
                    await self._calculate_sell_pnl(swing_id, sell_price, sell_qty, sell_amount)
                )

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

    async def _calculate_sell_pnl(
        self,
        swing_id: int,
        sell_price: Decimal,
        sell_qty: int,
        sell_amount: Decimal,
    ) -> dict:
        """
        매도 시 실현손익 계산

        Args:
            swing_id: 스윙 ID
            sell_price: 매도 체결가
            sell_qty: 매도 수량
            sell_amount: 매도 금액

        Returns:
            {"TOTAL_FEE": ..., "REALIZED_PNL": ...}
        """
        from app.domain.swing.entity import SwingTrade
        from sqlalchemy import select

        # 스윙의 매수 평균단가 조회
        result = await self.db.execute(
            select(SwingTrade.ENTRY_PRICE).where(SwingTrade.SWING_ID == swing_id)
        )
        entry_price = result.scalar_one_or_none()

        if not entry_price or entry_price <= 0:
            logger.warning(f"[SWING {swing_id}] 매수 평균단가 없음, 손익 계산 스킵")
            return {}

        avg_buy_price = Decimal(str(entry_price))

        # 제비용합계 (수수료 + 세금)
        total_fee = sell_amount * (self.COMMISSION_RATE + self.TAX_RATE)

        # 실현손익: (매도가 - 매수평균가) × 수량 - 제비용
        realized_pnl = (sell_price - avg_buy_price) * sell_qty - total_fee

        return {
            "TOTAL_FEE": round(total_fee, 2),
            "REALIZED_PNL": round(realized_pnl, 2),
        }

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

    async def get_trade_history_with_chart(
        self, user_id: str, swing_id: int, start_date, end_date
    ) -> dict:
        """
        매매 내역 + 주가 차트 + EMA20 데이터 통합 조회

        Args:
            user_id: 사용자 ID (소유권 검증)
            swing_id: 스윙 ID
            start_date: 조회 시작일 (date)
            end_date: 조회 종료일 (date)

        Returns:
            TradeHistoryWithChartResponse 구조의 dict
        """
        from app.domain.stock.service import StockService

        try:
            # 1. 스윙 조회 + 소유권 검증
            swing = await self.repo.find_swing_with_ownership(swing_id, user_id)

            if not swing:
                raise NotFoundError("스윙 전략", swing_id)

            # 2. 기간별 매매 내역 조회
            trade_start = datetime.combine(start_date, datetime.min.time())
            trade_end = datetime.combine(end_date, datetime.max.time())
            trades = await self.repo.find_by_swing_id_and_period(swing_id, trade_start, trade_end)
            trades_data = [
                TradeHistoryResponse.model_validate(t).model_dump() for t in trades
            ]

            # 3. 주가 데이터 조회 (EMA20 워밍업을 위해 2개월 전부터)
            warmup_start = datetime.combine(start_date, datetime.min.time()) - relativedelta(months=2)
            stock_service = StockService(self.db)
            price_days = await stock_service.get_stock_history(
                swing.MRKT_CODE, swing.ST_CODE, warmup_start
            )

            price_history = []
            ema20_history = []

            if price_days:
                prices_df = pd.DataFrame(price_days)

                # EMA20 계산 (백테스팅과 동일 패턴)
                close_arr = pd.to_numeric(prices_df["STCK_CLPR"], errors="coerce").values
                prices_df["ema20"] = ta.EMA(close_arr, timeperiod=20)

                # 조회 기간만 필터링
                date_start = start_date.strftime("%Y%m%d")
                date_end = end_date.strftime("%Y%m%d")
                period_mask = (prices_df["STCK_BSOP_DATE"] >= date_start) & (
                    prices_df["STCK_BSOP_DATE"] <= date_end
                )
                period_df = prices_df.loc[period_mask].copy()

                price_df = period_df[
                    ["STCK_BSOP_DATE", "STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR", "ACML_VOL"]
                ]
                price_history = price_df.where(price_df.notna(), None).to_dict(orient="records")

                ema20_history = [
                    {
                        "STCK_BSOP_DATE": row.STCK_BSOP_DATE,
                        "ema20": round(row.ema20, 2) if pd.notna(row.ema20) else None,
                    }
                    for row in period_df.itertuples(index=False)
                ]

            return {
                "swing_id": swing_id,
                "st_code": swing.ST_CODE,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "trades": trades_data,
                "price_history": price_history,
                "ema20_history": ema20_history,
            }

        except (NotFoundError, PermissionDeniedError):
            raise
        except SQLAlchemyError as e:
            logger.error(f"매매 내역 차트 조회 실패: {e}", exc_info=True)
            raise DatabaseError("매매 내역 조회에 실패했습니다")

    async def get_trade_stats(self, user_id: str, swing_id: int) -> dict:
        """
        매매 통계 조회 (전체 기간)

        Args:
            user_id: 사용자 ID (소유권 검증)
            swing_id: 스윙 ID

        Returns:
            {"total_count": int, "buy_count": int, "sell_count": int}
        """
        try:
            swing = await self.repo.find_swing_with_ownership(swing_id, user_id)
            if not swing:
                raise NotFoundError("스윙 전략", swing_id)

            return await self.repo.count_by_swing_id(swing_id)

        except (NotFoundError, PermissionDeniedError):
            raise
        except SQLAlchemyError as e:
            logger.error(f"매매 통계 조회 실패: {e}", exc_info=True)
            raise DatabaseError("매매 통계 조회에 실패했습니다")

    async def get_trade_history_list(
        self, user_id: str, swing_id: int, page: int = 1, size: int = 100
    ) -> dict:
        """
        매매 내역 페이징 조회

        Args:
            user_id: 사용자 ID (소유권 검증)
            swing_id: 스윙 ID
            page: 페이지 번호 (1부터)
            size: 페이지 크기

        Returns:
            TradeHistoryPageResponse 구조의 dict
        """
        try:
            swing = await self.repo.find_swing_with_ownership(swing_id, user_id)
            if not swing:
                raise NotFoundError("스윙 전략", swing_id)

            trades, total_count = await self.repo.find_by_swing_id_paged(swing_id, page, size)
            trades_data = [
                TradeHistoryResponse.model_validate(t).model_dump() for t in trades
            ]

            return {
                "trades": trades_data,
                "total_count": total_count,
                "page": page,
                "size": size,
                "has_next": (page * size) < total_count,
            }

        except (NotFoundError, PermissionDeniedError):
            raise
        except SQLAlchemyError as e:
            logger.error(f"매매 내역 페이징 조회 실패: {e}", exc_info=True)
            raise DatabaseError("매매 내역 조회에 실패했습니다")