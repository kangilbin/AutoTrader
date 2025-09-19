from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, update, select
from app.model.table_create import Stock, StockHstr
from app.model.schemas.stock_model import StockResponse, StockCreate
from sqlalchemy.exc import SQLAlchemyError
from typing import List
import logging
from sqlalchemy.dialects.mysql import insert as mysql_insert
from datetime import datetime

# 초성 검색
async def select_stock_initial(db: AsyncSession, initial: str):
    try:
        query = text("""
                    SELECT *
                    FROM STOCK_INFO
                    WHERE NAME RLIKE make_search_pattern(:initial)
                    ORDER BY
                        CASE
                            WHEN REGEXP_INSTR(NAME, make_search_pattern(:initial)) = 1 THEN 1
                            WHEN REGEXP_INSTR(NAME, make_search_pattern(:initial)) > 1 THEN 2
                            ELSE 3
                            END,
                        REGEXP_INSTR(NAME, make_search_pattern(:initial)),
                        NAME
                    LIMIT 20
                """)
        rows = await db.execute(query, {"initial": initial})
    except SQLAlchemyError as e:
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return [StockResponse.model_validate(row).model_dump() for row in rows]



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
async def update_stock(db: AsyncSession, stock_data: dict):
    try:
        query = (
            update(Stock)
            .filter(Stock.ST_CODE == stock_data['ST_CODE'])
            .values(**stock_data)
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
async def insert_bulk_stock_hstr(db: AsyncSession, stock_hstr_data: List[dict], st_code: str = None) -> int:
    try:
        # INSERT IGNORE를 사용하여 중복 데이터 무시
        
        
        # MySQL의 INSERT IGNORE 사용
        query = mysql_insert(StockHstr).values(stock_hstr_data)
        query = query.on_duplicate_key_update(
            STCK_OPRC=query.inserted.STCK_OPRC,
            STCK_HGPR=query.inserted.STCK_HGPR,
            STCK_LWPR=query.inserted.STCK_LWPR,
            STCK_CLPR=query.inserted.STCK_CLPR,
            ACML_VOL=query.inserted.ACML_VOL,
            MOD_DT=query.inserted.REG_DT
        )
        
        result = await db.execute(query)
        await db.commit()
        
        # 입력된 데이터 개수 반환 (실제 삽입 여부와 관계없이)
        return len(stock_hstr_data)
        
    except SQLAlchemyError as e:
        await db.rollback()
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return len(stock_hstr_data)


# 이평선 데이터 조회
async def select_stock_hstr(db: AsyncSession, code: str, start_date: datetime):
    try:
        query = (
            select(StockHstr)
            .filter(StockHstr.ST_CODE == code)
            .filter(StockHstr.STCK_BSOP_DATE >= start_date)
            .order_by(StockHstr.STCK_BSOP_DATE.asc())
        )
        result = await db.execute(query)
    except SQLAlchemyError as e:
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return [StockResponse.model_validate(row).model_dump() for row in result]