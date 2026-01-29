"""
Stock Service - 비즈니스 로직 및 트랜잭션 관리
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from typing import List
from datetime import datetime
import logging

from app.domain.stock.repository import StockRepository
from app.exceptions import DatabaseError, NotFoundError

logger = logging.getLogger(__name__)


class StockService:
    """종목 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = StockRepository(db)

    async def search_stock(self, query: str) -> List[dict]:
        """종목 검색 (초성)"""
        return await self.repo.search_by_initial(query)

    async def get_stock_info(self, mrkt_code: str, st_code: str) -> dict:
        """종목 정보 조회"""
        stock = await self.repo.find_by_code(mrkt_code, st_code)
        if not stock:
            raise NotFoundError("종목", st_code)
        return stock

    async def update_stock(self, mrkt_code: str, st_code: str, data: dict) -> dict:
        """종목 정보 수정"""
        try:
            data["MOD_DT"] = datetime.now()
            result = await self.repo.update(mrkt_code, st_code, data)
            await self.db.commit()
            return result
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"종목 수정 실패: {e}", exc_info=True)
            raise DatabaseError("종목 수정에 실패했습니다")

    async def save_history_bulk(self, history_data: List[dict]) -> int:
        """일별 데이터 벌크 저장"""
        try:
            count = await self.repo.save_history_bulk(history_data)
            await self.db.commit()
            return count
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error(f"일별 데이터 저장 실패: {e}", exc_info=True)
            raise DatabaseError("일별 데이터 저장에 실패했습니다")

    async def get_stock_history(self, mrkt_code: str, st_code: str, start_date: datetime) -> List[dict]:
        """일별 데이터 조회"""
        try:
            return await self.repo.find_history(mrkt_code, st_code, start_date)
        except SQLAlchemyError as e:
            logger.error(f"일별 데이터 조회 실패: {e}", exc_info=True)
            raise DatabaseError("일별 조회를 실패했습니다")
