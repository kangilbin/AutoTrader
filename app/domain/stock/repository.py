"""
Stock Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, text, func
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
            WHERE ST_NM RLIKE make_search_pattern(:initial)
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

    async def get_foreign_net_buy_sum(
        self,
        symbol: str,
        start_date: str,
        end_date: Optional[str] = None
    ) -> int:
        """
        외국인 순매수량 합산

        Args:
            symbol: 종목 코드
            start_date: 시작일 (YYYYMMDD)
            end_date: 종료일 (YYYYMMDD, None이면 오늘까지)

        Returns:
            외국인 순매수량 합계
        """
        query = select(func.sum(StockHistoryModel.FRGN_NTBY_QTY))
        query = query.where(StockHistoryModel.ST_CODE == symbol)
        query = query.where(StockHistoryModel.STCK_BSOP_DATE >= start_date)

        if end_date:
            query = query.where(StockHistoryModel.STCK_BSOP_DATE <= end_date)

        result = await self.db.execute(query)
        return result.scalar() or 0

    async def get_stock_volume_sum(
        self,
        symbol: str,
        start_date: str,
        end_date: Optional[str] = None
    ) -> tuple[int, int]:
        """
        외국인 순매수량 및 누적 거래량 합산

        Args:
            symbol: 종목 코드
            start_date: 시작일 (YYYYMMDD)
            end_date: 종료일 (YYYYMMDD, None이면 오늘까지)

        Returns:
            (외국인 순매수량 합계, 거래량 합계)
        """
        query = select(
            func.sum(StockHistoryModel.FRGN_NTBY_QTY).label('total_frgn'),
            func.sum(StockHistoryModel.ACML_VOL).label('total_vol')
        )
        query = query.where(StockHistoryModel.ST_CODE == symbol)
        query = query.where(StockHistoryModel.STCK_BSOP_DATE >= start_date)

        if end_date:
            query = query.where(StockHistoryModel.STCK_BSOP_DATE <= end_date)

        result = await self.db.execute(query)
        data = result.one()
        return (data.total_frgn or 0, data.total_vol or 0)