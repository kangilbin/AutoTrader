"""
Order API Router
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated, Optional
from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.core.response import success_response
from app.domain.order.service import OrderService
from app.domain.order.schemas import OrderModifyRequest

router = APIRouter(prefix="/orders", tags=["Orders"])


def get_order_service(db: AsyncSession = Depends(get_db)) -> OrderService:
    """OrderService 의존성 주입"""
    return OrderService(db)


@router.get("/cancelable")
async def list_cancelable_orders(
    service: Annotated[OrderService, Depends(get_order_service)],
    user_id: Annotated[str, Depends(get_current_user)],
    fk100: Optional[str] = Query("", description="페이징 키 FK100"),
    nk100: Optional[str] = Query("", description="페이징 키 NK100")
):
    """정정/취소 가능 주문 내역 조회"""
    result = await service.get_cancelable_orders(user_id, fk100, nk100)
    return success_response("주문 내역 조회", result)


@router.patch("/{order_no}")
async def update_or_cancel_order(
    order_no: str,
    request: OrderModifyRequest,
    service: Annotated[OrderService, Depends(get_order_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """주문 정정/취소"""
    result = await service.modify_or_cancel_order(user_id, request)
    return success_response("정정/취소 완료", result)