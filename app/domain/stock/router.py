"""
Stock API Router
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.core.response import success_response
from app.domain.stock.service import StockService
from app.external import kis_api, foreign_api

router = APIRouter(prefix="/stocks", tags=["Stocks"])


def get_stock_service(db: AsyncSession = Depends(get_db)) -> StockService:
    """StockService 의존성 주입"""
    return StockService(db)


@router.get("")
async def search_stock(
    query: str,
    service: Annotated[StockService, Depends(get_stock_service)],
    mrkt_code: str = Query(None, description="시장 구분코드 (J:국내, NASD:나스닥)")
):
    """종목 검색"""
    stock_info = await service.search_stock(query, mrkt_code)
    return success_response("종목 코드 조회", stock_info)


@router.get("/price")
async def get_asking_price(
    st_code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[str, Depends(get_current_user)],
    mrkt_code: Annotated[str, Query(description="J:국내, NASD:나스닥")] = "J",
):
    """주식 호가 조회 (국내/나스닥)"""
    if mrkt_code == "NASD":
        response = await foreign_api.get_inquire_asking_price(user_id, st_code, db)
    else:
        response = await kis_api.get_inquire_asking_price(user_id, st_code, db)
    return success_response("주식 호가 조회", response)


@router.get("/ranking/fluctuation")
async def fluctuation_rank(
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[str, Depends(get_current_user)],
    rank_sort: Annotated[str, Query(description="0:상승율순, 1:하락율순")] = "0",
    prc_cls: Annotated[str, Query(description="상승율(0:저가대비,1:종가대비) / 하락율(0:고가대비,1:종가대비) / 기타(0:전체)")] = "1",
    mrkt_code: Annotated[str, Query(description="J:국내, NASD:나스닥")] = "J",
):
    """등락률 순위 (국내/나스닥)"""
    if mrkt_code == "NASD":
        response = await foreign_api.get_fluctuation_rank(user_id, db, rank_sort)
    else:
        response = await kis_api.get_fluctuation_rank(user_id, db, rank_sort, prc_cls)
    return success_response("등락률 순위 조회", response)


@router.get("/ranking/volume")
async def volume_rank(
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[str, Depends(get_current_user)],
    blng_cls: Annotated[str, Query(description="0:평균거래량, 1:거래증가율, 3:거래금액순")] = "3",
    mrkt_code: Annotated[str, Query(description="J:국내, NASD:나스닥")] = "J",
):
    """거래량 순위 (국내/나스닥)"""
    if mrkt_code == "NASD":
        response = await foreign_api.get_volume_rank(user_id, db)
    else:
        response = await kis_api.get_volume_rank(user_id, db, blng_cls)
    return success_response("거래량 순위 조회", response)


@router.get("/ranking/volume-power")
async def volume_power_rank(
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[str, Depends(get_current_user)],
    input_iscd: Annotated[str, Query(description="0000:전체, 0001:거래소, 1001:코스닥, 2001:코스피2000")] = "0000",
    mrkt_code: Annotated[str, Query(description="J:국내, NASD:나스닥")] = "J",
):
    """체결강도 순위 (국내/나스닥)"""
    if mrkt_code == "NASD":
        response = await foreign_api.get_volume_power_rank(user_id, db)
    else:
        response = await kis_api.get_volume_power_rank(user_id, db, input_iscd)
    return success_response("체결강도 순위 조회", response)
