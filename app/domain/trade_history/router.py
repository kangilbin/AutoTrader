"""
Trade History API Router
"""
from datetime import datetime
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
    year: Optional[int] = Query(default=None, description="조회 연도 (기본: 현재 연도)")
):
    """매매 내역 + 차트 데이터 조회 (1년 단위 페이징)"""
    if year is None:
        year = datetime.now().year

    result = await service.get_trade_history_with_chart(user_id, swing_id, year)
    return success_response("매매 내역 조회 완료", result)
