from sqlalchemy.ext.asyncio import AsyncSession
from crud.StockCrud import select_stock_initial


async def get_stock_initial(db: AsyncSession, initial: str):
    return await select_stock_initial(db, initial)