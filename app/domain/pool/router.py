"""
Pool API Router - 풀(총자산 공유) 매수 설정
"""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.core.response import success_response
from app.domain.pool.schemas import PoolParkRequest, PoolSettingUpdateRequest
from app.domain.pool.service import PoolService

router = APIRouter(prefix="/pool", tags=["Pool"])


def get_pool_service(db: AsyncSession = Depends(get_db)) -> PoolService:
    """PoolService 의존성 주입"""
    return PoolService(db)


@router.get("")
async def get_pool_settings(
    account_no: str = Query(..., description="계좌번호"),
    service: Annotated[PoolService, Depends(get_pool_service)] = None,
    user_id: Annotated[str, Depends(get_current_user)] = None
):
    """풀 설정 조회 (국내·미국) — 저장된 설정이 없으면 기본값(꺼짐)"""
    result = await service.get_settings(user_id, account_no)
    return success_response("풀 설정 조회 완료", result)


@router.put("")
async def update_pool_setting(
    request: PoolSettingUpdateRequest,
    service: Annotated[PoolService, Depends(get_pool_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """풀 설정 저장 — 보낸 항목만 반영"""
    result = await service.update_setting(user_id, request)
    return success_response("풀 설정 저장 완료", result)


@router.get("/park")
async def get_parking(
    service: Annotated[PoolService, Depends(get_pool_service)],
    user_id: Annotated[str, Depends(get_current_user)],
    account_no: str = Query(..., description="계좌번호"),
    market: Literal['KR', 'US'] = Query(..., description="풀 시장 (KR/US)"),
):
    """파킹 ETF 보유 현황 — 풀이 꺼져 있거나 파킹 종목이 없으면 400"""
    result = await service.get_parking(user_id, account_no, market)
    return success_response("파킹 현황 조회 완료", result)


@router.post("/park")
async def park_idle_cash(
    request: PoolParkRequest,
    service: Annotated[PoolService, Depends(get_pool_service)],
    user_id: Annotated[str, Depends(get_current_user)],
):
    """유휴 현금(남길 현금 제외)을 파킹 ETF로 전환 — 상태는 응답의 STATUS 로 구분"""
    result = await service.park(user_id, request.ACCOUNT_NO, request.MARKET)
    return success_response("파킹 처리 완료", result)
