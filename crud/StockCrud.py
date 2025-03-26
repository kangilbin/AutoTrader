from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, update, select
from model.TableCreate import Stock
from model.schemas.StockModel import StockResponse, StockCreate
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


async def select_stock(db: AsyncSession, code: str) -> StockResponse:
    try:
        query = select(Stock).filter(Stock.USER_ID == code)
        result = await db.execute(query)
        db_user = result.scalars().first()
    except SQLAlchemyError as e:
        logger.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return StockResponse.from_orm(db_user)


# 종목 update
async def update_stock(db: AsyncSession, stock_data: StockCreate):
    try:
        query = (
            update(Stock)
            .filter(Stock.ST_CODE == stock_data.ST_CODE)
            .values(**stock_data.dict())
            .execution_options(synchronize_session=False)
        )
        await db.execute(query)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return stock_data
