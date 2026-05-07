"""
백테스팅 관련 API 라우터
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.domain.swing.schemas import SwingCreateRequest
from app.domain.swing.backtest.backtest_service import run_backtest
from app.core.response import success_response
from app.exceptions import DatabaseError

router = APIRouter(prefix="/backtesting", tags=["Backtest"])


@router.post("")
async def request_backtest(
    swing: SwingCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """백테스팅 실행 및 결과 반환"""
    try:
        result = await run_backtest(db, swing)
        return success_response("백테스팅 완료", result)
    except Exception as e:
        raise DatabaseError("백테스팅 처리 중 오류가 발생했습니다", operation="backtest", original_error=e)
