"""
Stock Review API Router — 스윙 등록 전 Gemini 종목 분석 (국내·미국)

분석은 1분 이상 걸리므로 요청 즉시 RUNNING 을 돌려주고 백그라운드에서 처리한다.
프론트는 목록을 폴링해 상태(RUNNING/DONE/FAILED)를 확인한다.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.core.response import success_response
from app.domain.stock_review.schemas import StockReviewRequest
from app.domain.stock_review.service import StockReviewService

router = APIRouter(prefix="/stock-review", tags=["StockReview"])


def get_stock_review_service(db: AsyncSession = Depends(get_db)) -> StockReviewService:
    return StockReviewService(db)


@router.post("")
async def request_review(
    request: StockReviewRequest,
    service: Annotated[StockReviewService, Depends(get_stock_review_service)],
    user_id: Annotated[str, Depends(get_current_user)],
):
    """분석 요청 — 캐시된 결과가 있으면 DONE, 아니면 RUNNING"""
    result = await service.request_review(user_id, request.ST_CODE, request.MRKT_CODE)
    return success_response("종목 분석 요청", result)


@router.get("")
async def list_reviews(
    service: Annotated[StockReviewService, Depends(get_stock_review_service)],
    user_id: Annotated[str, Depends(get_current_user)],
):
    """내 분석 목록 (최근 24시간)"""
    result = await service.list_reviews(user_id)
    return success_response("종목 분석 목록", result)


@router.get("/{st_code}")
async def get_review(
    st_code: str,
    service: Annotated[StockReviewService, Depends(get_stock_review_service)],
    user_id: Annotated[str, Depends(get_current_user)],
):
    """분석 상태·결과 (완료 결과를 조회하면 확인 처리)"""
    result = await service.get_review(user_id, st_code)
    return success_response("종목 분석 결과", result)
