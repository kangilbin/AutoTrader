from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, update, select, insert
from app.model.TableCreate import Stock, StockHstr
from app.model.schemas.StockModel import StockResponse, StockCreate
from sqlalchemy.exc import SQLAlchemyError
from typing import List
import logging


# 초성 검색
async def select_stock_initial(db: AsyncSession, initial: str):
    try:
        query = text(f"SELECT * FROM STOCK_INFO WHERE MATCH(NAME) AGAINST(:initial IN BOOLEAN MODE)")
        result = await db.execute(query, {"initial": initial + "*"})
    except SQLAlchemyError as e:
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return result.scalars().all()


async def select_stock(db: AsyncSession, code: str) -> StockResponse:
    try:
        query = select(Stock).filter(Stock.ST_CODE == code)
        result = await db.execute(query)
        db_user = result.scalars().first()
    except SQLAlchemyError as e:
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return StockResponse.model_validate(db_user).model_dump()


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
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return stock_data


# 주식 일별 데이터 적재
async def insert_bulk_stock_hstr(db: AsyncSession, stock_hstr_data: List[dict]) -> int:
    try:
        query = insert(StockHstr).values(stock_hstr_data)
        await db.execute(query)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return len(stock_hstr_data)


# 이평선 데이터 조회
async def select_stock_hstr(db: AsyncSession, code: str, long_term: int):
    try:
        query = (
            select(StockHstr)
            .filter(StockHstr.USER_ID == code)
            .order_by(StockHstr.STCK_BSOP_DATE.desc())
            .limit(long_term * 3)
        )
        result = await db.execute(query)
    except SQLAlchemyError as e:
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return result.scalars().all()

