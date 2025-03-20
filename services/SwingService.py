from sqlalchemy.ext.asyncio import AsyncSession
from crud.SwingCrud import insert_swing_trade, select_swing_trade, select_swing_trade_account, list_all_swing
from model.schemas.SwingModel import SwingCreate


# 스윙 전략 등록
async def create_swing_trade(db: AsyncSession, swing_data: SwingCreate):
    return await insert_swing_trade(db, swing_data)

# 스윙 전략 조회
async def get_swing_trade(db: AsyncSession, swing_id: int):
    return await select_swing_trade(db, swing_id)

#스윙 전략 조회(계좌 번호)
async def get_swing_trade_account_no(db: AsyncSession, account_no: str):
    return await select_swing_trade_account(db, account_no)


# 모든 등록된 스윙 조회
async def get_all_swing(db: AsyncSession):
    return await list_all_swing(db)
