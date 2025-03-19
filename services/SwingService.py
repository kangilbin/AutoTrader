from sqlalchemy.ext.asyncio import AsyncSession

from api.LocalStockApi import get_stock_balance
from crud.StockCrud import select_stock_initial
from model.schemas.SwingModel import SwingCreate


# 스윙 전략 등록
async def create_swing_trade(db: AsyncSession, user_id: str, swing_data: SwingCreate):
    amt = await get_stock_balance(user_id)['output']['ord_psbl_cash']
    return await insert_swing_trade(db, swing_data)
