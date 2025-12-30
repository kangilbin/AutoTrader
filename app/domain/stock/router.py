"""
Stock API Router
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.domain.stock.service import StockService
from app.external.kis_api import get_inquire_asking_price

router = APIRouter(prefix="/stocks", tags=["Stocks"])


def get_stock_service(db: AsyncSession = Depends(get_db)) -> StockService:
    """StockService 의존성 주입"""
    return StockService(db)


@router.get("")
async def search_stock(
    query: str,
    service: Annotated[StockService, Depends(get_stock_service)]
):
    """종목 검색"""
    stock_info = await service.search_stock(query)
    return {"message": "종목 코드 조회", "data": stock_info}


@router.get("/price")
async def get_asking_price(
    st_code: str,
    user_id: Annotated[str, Depends(get_current_user)]
):
    """주식 호가 조회"""
    response = await get_inquire_asking_price(user_id, st_code)
    return {"message": "주식 호가 조회", "data": response}