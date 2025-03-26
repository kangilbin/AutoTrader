from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from sqlalchemy import select
from model.TableCreate import Stock
from model.schemas.StockModel import StockResponse
from sqlalchemy.exc import SQLAlchemyError
import logging

logger = logging.getLogger(__name__)


# 초성 검색
async def select_stock_initial(db: AsyncSession, initial: str):
    try:
        query = text(f"SELECT * FROM STOCK_INFO WHERE MATCH(NAME) AGAINST(:initial IN BOOLEAN MODE)")
        result = await db.execute(query, {"initial": initial + "*"})
    except SQLAlchemyError as e:
        logger.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return result.scalars().all()


async def select_stock(db: AsyncSession, code: str):
    try:
        query = select(Stock).filter(Stock.USER_ID == code)
        result = await db.execute(query)
        db_user = result.scalars().first()
    except SQLAlchemyError as e:
        logger.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return StockResponse.from_orm(db_user).dict()
