from sqlalchemy.ext.asyncio import AsyncSession
from crud.StockCrud import select_stock_initial, select_stock, update_stock, select_stock_hstr
from model.schemas.StockModel import StockResponse, StockCreate
from datetime import datetime


# 초성 검색
async def get_stock_initial(db: AsyncSession, initial: str):
    return await select_stock_initial(db, initial)


# 종목 조회
async def get_stock_info(db: AsyncSession, stock_code: str) -> StockResponse:
    return await select_stock(db, stock_code)


# 종목 수정
async def mod_stock(db: AsyncSession, stock_data: StockCreate):
    stock_data.MOD_DT = datetime.now()
    return await update_stock(db, stock_data)


# 이평선 데이터 조회
async def get_avg_stock_price(db: AsyncSession, code: str, short_term: int, medium_term: int, long_term: int):
    return await select_stock_hstr(db, code, short_term, medium_term, long_term)