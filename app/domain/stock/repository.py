"""
Stock Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from typing import Optional, List
from datetime import datetime

from app.common.database import StockModel, StockHistoryModel
from app.domain.stock.schemas import StockResponse, StockHistoryResponse
import logging

logger = logging.getLogger(__name__)


class StockRepository:
    """종목 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_by_code(self, code: str) -> Optional[dict]:
        """종목 코드로 조회"""
        query = select(StockModel).filter(StockModel.ST_CODE == code)
        result = await self.db.execute(query)
        db_stock = result.scalars().first()
        if not db_stock:
            return None
        return StockResponse.model_validate(db_stock).model_dump()

    async def search_by_initial(self, initial: str) -> List[dict]:
        """초성 검색"""
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
            /*LIMIT 20*/
        """)
        rows = await self.db.execute(query, {"initial": initial})
        return [StockResponse.model_validate(row).model_dump() for row in rows]

    async def update(self, st_code: str, data: dict) -> dict:
        """종목 정보 수정 (flush만 수행)"""
        query = (
            update(StockModel)
            .filter(StockModel.ST_CODE == st_code)
            .values(**data)
            .execution_options(synchronize_session=False)
        )
        await self.db.execute(query)
        await self.db.flush()
        return data

    async def save_history_bulk(self, history_data: List[dict]) -> int:
        """일별 데이터 벌크 저장 (flush만 수행)"""
        query = mysql_insert(StockHistoryModel).values(history_data)
        query = query.on_duplicate_key_update(
            STCK_OPRC=query.inserted.STCK_OPRC,
            STCK_HGPR=query.inserted.STCK_HGPR,
            STCK_LWPR=query.inserted.STCK_LWPR,
            STCK_CLPR=query.inserted.STCK_CLPR,
            ACML_VOL=query.inserted.ACML_VOL,
            MOD_DT=query.inserted.REG_DT
        )
        await self.db.execute(query)
        await self.db.flush()
        return len(history_data)

    async def find_history(self, code: str, start_date: datetime) -> List[dict]:
        """일별 데이터 조회"""
        query = (
            select(StockHistoryModel)
            .filter(StockHistoryModel.ST_CODE == code)
            .filter(StockHistoryModel.STCK_BSOP_DATE >= start_date.strftime('%Y%m%d'))
            .order_by(StockHistoryModel.STCK_BSOP_DATE.asc())
        )
        result = await self.db.execute(query)
        return [StockHistoryResponse.model_validate(row).model_dump() for row in result.scalars().all()]