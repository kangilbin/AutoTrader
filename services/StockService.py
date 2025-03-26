from sqlalchemy.ext.asyncio import AsyncSession
from crud.StockCrud import select_stock_initial, select_stock, update_stock
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
