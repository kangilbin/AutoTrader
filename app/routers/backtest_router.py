"""
백테스팅 관련 API 라우터
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.swing.schemas import SwingCreateRequest
from app.swing.backtest.backtest_service import start_backtest_job, get_backtest_job
from app.core.response import success_response
from app.common.exceptions import BusinessException, NotFoundException

router = APIRouter(prefix="/backtest", tags=["Backtest"])


@router.post("")
async def request_backtest(
    swing: SwingCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """
    백테스팅 요청
    백그라운드 잡으로 실행하고 job_id 반환
    결과는 GET /backtest/{job_id}로 조회
    """
    try:
        job_id = await start_backtest_job(db, swing)
        return success_response("백테스팅 요청 접수", {
            "status": "accepted",
            "job_id": job_id
        })
    except Exception as e:
        raise BusinessException(str(e))


@router.get("/{job_id}")
async def get_backtest_result(job_id: str):
    """백테스팅 결과 조회"""
    job = await get_backtest_job(job_id)
    if not job:
        raise NotFoundException("백테스팅 작업", job_id)
    return success_response("백테스팅 결과", job)
