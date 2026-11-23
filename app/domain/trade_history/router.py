"""
Trade History API Router
"""
from datetime import date
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated, Optional

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.core.response import success_response
from app.domain.trade_history.service import TradeHistoryService

router = APIRouter(prefix="/trade-history", tags=["Trade History"])


def get_trade_history_service(db: AsyncSession = Depends(get_db)) -> TradeHistoryService:
    """TradeHistoryService 의존성 주입"""
    return TradeHistoryService(db)


@router.get("/{swing_id}")
async def get_trade_history_with_chart(
    swing_id: int,
    service: Annotated[TradeHistoryService, Depends(get_trade_history_service)],
    user_id: Annotated[str, Depends(get_current_user)],
    start_date: Optional[date] = Query(default=None, description="조회 시작일 (YYYY-MM-DD)"),
    end_date: Optional[date] = Query(default=None, description="조회 종료일 (YYYY-MM-DD)")
):
    """매매 내역 + 차트 데이터 조회 (기간 지정)"""
    # 기본값: 현재 연도 1월 1일 ~ 오늘
    today = date.today()
    if start_date is None:
        start_date = date(today.year, 1, 1)
    if end_date is None:
        end_date = today

    result = await service.get_trade_history_with_chart(user_id, swing_id, start_date, end_date)
    return success_response("매매 내역 조회 완료", result)
