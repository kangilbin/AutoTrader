"""
Stock Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from typing import Optional, List
from datetime import datetime

from app.domain.stock.entity import Stock, StockHistory
from app.domain.stock.schemas import StockResponse, StockHistoryResponse
import logging

logger = logging.getLogger(__name__)


class StockRepository:
    """종목 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_data_target_stocks(self, overseas: bool = None) -> List[Stock]:
        """DATA_YN = 'Y'인 종목 목록 조회 (시장 구분 필터링)"""
        query = select(Stock).filter(
            Stock.DATA_YN == 'Y',
            Stock.DEL_YN == 'N'
        )
        if overseas is True:
            query = query.filter(Stock.MRKT_CODE == 'NASD')
        elif overseas is False:
            query = query.filter(Stock.MRKT_CODE != 'NASD')
        result = await self.db.execute(query)
        return result.scalars().all()

    async def find_by_code(self, mrkt_code: str, st_code: str) -> Optional[dict]:
        """종목 코드로 조회"""
        query = select(Stock).filter(
            Stock.MRKT_CODE == mrkt_code,
            Stock.ST_CODE == st_code
        )
        result = await self.db.execute(query)
        db_stock = result.scalars().first()
        if not db_stock:
            return None
        return StockResponse.model_validate(db_stock).model_dump()

    async def search_by_initial(self, initial: str, mrkt_code: str = None) -> List[dict]:
        """초성 검색"""
        mrkt_filter = "AND MRKT_CODE = :mrkt_code" if mrkt_code else ""
        query = text(f"""
            SELECT *
            FROM STOCK_INFO
            WHERE ST_NM RLIKE make_search_pattern(:initial)
            {mrkt_filter}
            ORDER BY
                CASE
                    WHEN REGEXP_INSTR(ST_NM, make_search_pattern(:initial)) = 1 THEN 1
                    WHEN REGEXP_INSTR(ST_NM, make_search_pattern(:initial)) > 1 THEN 2
                    ELSE 3
                END,
                REGEXP_INSTR(ST_NM, make_search_pattern(:initial)),
                ST_NM
            LIMIT 20
        """)
        params = {"initial": initial}
        if mrkt_code:
            params["mrkt_code"] = mrkt_code
        rows = await self.db.execute(query, params)
        return [StockResponse.model_validate(row).model_dump() for row in rows]

    async def update(self, mrkt_code: str, st_code: str, data: dict) -> dict:
        """종목 정보 수정 (flush만 수행)"""
        query = (
            update(Stock)
            .filter(Stock.MRKT_CODE == mrkt_code, Stock.ST_CODE == st_code)
            .values(**data)
            .execution_options(synchronize_session=False)
        )
        await self.db.execute(query)
        await self.db.flush()
        return data

    async def save_history_bulk(self, history_data: List[dict]) -> int:
        """일별 데이터 벌크 저장 (flush만 수행)"""
        query = mysql_insert(StockHistory).values(history_data)
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

    async def find_history(self, mrkt_code: str, st_code: str, start_date: datetime) -> List[dict]:
        """일별 데이터 조회"""
        query = (
            select(StockHistory)
            .filter(StockHistory.MRKT_CODE == mrkt_code, StockHistory.ST_CODE == st_code)
            .filter(StockHistory.STCK_BSOP_DATE >= start_date.strftime('%Y%m%d'))
            .order_by(StockHistory.STCK_BSOP_DATE.asc())
        )
        result = await self.db.execute(query)
        return [StockHistoryResponse.model_validate(row).model_dump() for row in result.scalars().all()]
