from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text


# 초성 검색
async def select_stock_initial(db: AsyncSession, initial: str):
    query = text(f"SELECT * FROM STOCK_INFO WHERE MATCH(NAME) AGAINST(:initial IN BOOLEAN MODE)")
    result = await db.execute(query, {"initial": initial + "*"})
    return result.scalars().all()


