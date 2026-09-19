"""
Swing Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, text, and_, func
from typing import Optional, List
from decimal import Decimal
from app.core.market_code import is_overseas, US_MARKETS
from app.domain.swing.entity import SwingTrade, EmaOption
from app.domain.stock.entity import Stock
from app.domain.account.entity import Account
from app.domain.swing.schemas import SwingResponse
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class SwingRepository:
    """스윙 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_by_id(self, swing_id: int) -> Optional[SwingTrade]:
        """스윙 조회"""
        query = select(SwingTrade).filter(SwingTrade.SWING_ID == swing_id)
        result = await self.db.execute(query)
        return result.scalars().first()

    async def find_by_id_with_ownership(self, user_id: str, swing_id: int) -> Optional[SwingTrade]:
        """스윙 조회 + 소유권 검증

        SWING_TRADE에는 USER_ID가 없다. 소유자는 ACCOUNT_NO로 ACCOUNT를 조인해야
        나온다 — 이 조인을 빼면 스윙 ID만 아는 사용자가 남의 스윙을 읽고 고칠 수 있다.

        scalar_one_or_none()을 쓰지 않는 이유: ACCOUNT에 (USER_ID, ACCOUNT_NO)
        유니크 제약이 없어 같은 계좌가 여러 인증키로 등록될 수 있고(앱키 교체 시
        실제로 발생), 그때 조인 결과가 2행이 되어 MultipleResultsFound로 터진다.
        """
        query = (
            select(SwingTrade)
            .join(Account, SwingTrade.ACCOUNT_NO == Account.ACCOUNT_NO)
            .filter(SwingTrade.SWING_ID == swing_id, Account.USER_ID == user_id)
        )
        result = await self.db.execute(query)
        return result.scalars().first()

    async def find_by_account_and_stock(self, account_no: str, mrkt_code: str, st_code: str) -> Optional[SwingTrade]:
        """계좌번호 + 시장코드 + 종목코드로 스윙 조회"""
        query = select(SwingTrade).filter(
            SwingTrade.ACCOUNT_NO == account_no,
            SwingTrade.MRKT_CODE == mrkt_code,
            SwingTrade.ST_CODE == st_code
        )
        result = await self.db.execute(query)
        return result.scalars().first()

    async def find_all_by_account_no(self, account_no: str, mrkt_code: str = None) -> List[dict]:
        """계좌번호로 스윙 목록 조회"""
        query = (
            select(*SwingTrade.__table__.columns, Stock.ST_NM)
            .join(
                Stock,
                and_(
                    SwingTrade.MRKT_CODE == Stock.MRKT_CODE,
                    SwingTrade.ST_CODE == Stock.ST_CODE
                ),
                isouter=True
            )
            .filter(SwingTrade.ACCOUNT_NO == account_no)
        )
        if mrkt_code:
            if is_overseas(mrkt_code):
                query = query.filter(SwingTrade.MRKT_CODE.in_(US_MARKETS))
            else:
                query = query.filter(SwingTrade.MRKT_CODE.notin_(US_MARKETS))
        result = await self.db.execute(query)
        return [SwingResponse(**row).model_dump() for row in result.mappings().all()]


    async def find_active_swings(self) -> List:
        """활성화된 스윙 목록 조회 (배치용)

        ⚠️ ACCOUNT에 (USER_ID, ACCOUNT_NO) 유니크 제약이 없다. 계좌번호로만 조인하면
        같은 계좌가 여러 인증키로 등록된 경우(앱키 교체 시 발생) 스윙 1건이 여러 행으로
        불어나고, 배치는 중복 제거 없이 gather로 돌리므로 같은 스윙에 주문이 두 번 나간다.
        ON 절에서 최신 ACCOUNT_ID 1건으로 고정해 이를 막는다
        (find_auth_id_by_account_no의 '최신 등록분 사용' 규칙과 동일).
        """
        query = text(
            "SELECT ST.*, A.USER_ID, U.API_KEY, U.SECRET_KEY "
            "FROM SWING_TRADE ST "
            "LEFT JOIN ACCOUNT A ON ST.ACCOUNT_NO = A.ACCOUNT_NO "
            "AND A.ACCOUNT_ID = (SELECT MAX(A2.ACCOUNT_ID) FROM ACCOUNT A2 WHERE A2.ACCOUNT_NO = ST.ACCOUNT_NO) "
            "LEFT JOIN AUTH_KEY U ON A.USER_ID = U.USER_ID AND A.AUTH_ID = U.AUTH_ID "
            "WHERE ST.USE_YN = 'Y'"
        )
        result = await self.db.execute(query)
        return result.all()

    async def find_active_domestic_swings(self) -> List:
        """활성화된 국내 스윙 목록 조회"""
        query = text(
            "SELECT ST.*, A.USER_ID, U.API_KEY, U.SECRET_KEY "
            "FROM SWING_TRADE ST "
            "JOIN ACCOUNT A ON ST.ACCOUNT_NO = A.ACCOUNT_NO "
            "AND A.ACCOUNT_ID = (SELECT MAX(A2.ACCOUNT_ID) FROM ACCOUNT A2 WHERE A2.ACCOUNT_NO = ST.ACCOUNT_NO) "
            "JOIN AUTH_KEY U ON A.USER_ID = U.USER_ID AND A.AUTH_ID = U.AUTH_ID "
            "WHERE ST.USE_YN = 'Y' AND ST.MRKT_CODE IN ('J', 'NX', 'UN')"
        )
        result = await self.db.execute(query)
        return result.all()

    async def find_active_overseas_swings(self) -> List:
        """활성화된 해외 스윙 목록 조회"""
        query = text(
            "SELECT ST.*, A.USER_ID, U.API_KEY, U.SECRET_KEY "
            "FROM SWING_TRADE ST "
            "JOIN ACCOUNT A ON ST.ACCOUNT_NO = A.ACCOUNT_NO "
            "AND A.ACCOUNT_ID = (SELECT MAX(A2.ACCOUNT_ID) FROM ACCOUNT A2 WHERE A2.ACCOUNT_NO = ST.ACCOUNT_NO) "
            "JOIN AUTH_KEY U ON A.USER_ID = U.USER_ID AND A.AUTH_ID = U.AUTH_ID "
            "WHERE ST.USE_YN = 'Y' AND ST.MRKT_CODE IN ('NYS', 'NAS', 'AMS')"
        )
        result = await self.db.execute(query)
        return result.all()

    async def save(self, swing: SwingTrade) -> SwingTrade:
        """스윙 저장 (flush만 수행)"""
        self.db.add(swing)
        await self.db.flush()
        await self.db.refresh(swing)
        return swing

    async def save_ema_option(self, ema: EmaOption) -> EmaOption:
        """이평선 옵션 저장 (flush만 수행)"""
        self.db.add(ema)
        await self.db.flush()
        await self.db.refresh(ema)
        return ema

    async def exists_by_account_stock(self, account_no: str, st_code: str) -> bool:
        """계좌+종목에 남아 있는 스윙이 있는지 확인 (EMA_OPT 정리 판단용)

        SWING_TRADE의 유니크 키는 (ACCOUNT_NO, MRKT_CODE, ST_CODE)지만 EMA_OPT는
        (ACCOUNT_NO, ST_CODE)라 시장코드가 없다. 같은 계좌·종목의 J/NX/UN 스윙이
        EMA_OPT 한 행을 공유하므로, 형제가 남아 있으면 지우면 안 된다.
        """
        query = select(SwingTrade.SWING_ID).filter(
            SwingTrade.ACCOUNT_NO == account_no, SwingTrade.ST_CODE == st_code
        ).limit(1)
        result = await self.db.execute(query)
        return result.scalars().first() is not None

    async def delete_ema_option(self, account_no: str, st_code: str) -> bool:
        """이평선 옵션 삭제 (flush만 수행)

        EMA_OPT의 PK는 (ACCOUNT_NO, ST_CODE)다. 존재하지 않는 SWING_ID 컬럼으로
        필터하면 AttributeError가 난다.
        """
        query = delete(EmaOption).filter(
            EmaOption.ACCOUNT_NO == account_no, EmaOption.ST_CODE == st_code
        )
        result = await self.db.execute(query)
        await self.db.flush()
        return result.rowcount > 0


    async def update(self, swing_id: int, data: dict) -> Optional[SwingTrade]:
        """스윙 수정 (flush만 수행)"""
        query = (
            update(SwingTrade)
            .filter(SwingTrade.SWING_ID == swing_id)
            .values(**data)
            .execution_options(synchronize_session=False)
        )
        await self.db.execute(query)
        await self.db.flush()
        return await self.db.get(SwingTrade, swing_id)

    async def delete(self, swing_id: int) -> bool:
        """스윙 삭제 (flush만 수행)"""
        query = delete(SwingTrade).filter(SwingTrade.SWING_ID == swing_id)
        result = await self.db.execute(query)
        await self.db.flush()
        return result.rowcount > 0

    async def find_by_account_nos(self, account_nos: List[str]) -> List:
        """계좌번호 목록에 속한 스윙 전체 조회 (동반 삭제 전 영향 확인용)"""
        if not account_nos:
            return []
        query = select(SwingTrade).filter(SwingTrade.ACCOUNT_NO.in_(account_nos))
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def delete_by_account_nos(self, account_nos: List[str]) -> int:
        """계좌번호 목록에 속한 스윙 일괄 삭제 (flush만 수행)"""
        if not account_nos:
            return 0
        query = delete(SwingTrade).filter(SwingTrade.ACCOUNT_NO.in_(account_nos))
        result = await self.db.execute(query)
        await self.db.flush()
        return result.rowcount

    async def delete_ema_options_by_account_nos(self, account_nos: List[str]) -> int:
        """계좌번호 목록에 속한 이평선 옵션 일괄 삭제 (flush만 수행)

        EMA_OPT의 PK는 (ACCOUNT_NO, ST_CODE)라 계좌 단위로 지울 수 있다.
        """
        if not account_nos:
            return 0
        query = delete(EmaOption).filter(EmaOption.ACCOUNT_NO.in_(account_nos))
        result = await self.db.execute(query)
        await self.db.flush()
        return result.rowcount

    async def get_total_init_amount(
        self, account_no: str, overseas: bool = False, exclude_swing_id: int = None
    ) -> Decimal:
        """계좌별 INIT_AMOUNT 합계 조회"""
        query = select(func.coalesce(func.sum(SwingTrade.INIT_AMOUNT), 0)).filter(
            SwingTrade.ACCOUNT_NO == account_no,
            SwingTrade.USE_YN == 'Y'
        )
        if overseas:
            query = query.filter(SwingTrade.MRKT_CODE.in_(US_MARKETS))
        else:
            query = query.filter(SwingTrade.MRKT_CODE.notin_(US_MARKETS))

        if exclude_swing_id is not None:
            query = query.filter(SwingTrade.SWING_ID != exclude_swing_id)

        result = await self.db.execute(query)
        return Decimal(result.scalar_one())

    async def find_active_stock_codes(self) -> List[tuple]:
        """
        활성화된 종목 코드 목록 조회 (EMA 캐시 워밍업용)

        Returns:
            USE_YN='Y'인 고유 (MRKT_CODE, ST_CODE) 튜플 리스트
        """
        query = (
            select(SwingTrade.MRKT_CODE, SwingTrade.ST_CODE)
            .filter(SwingTrade.USE_YN == 'Y')
            .distinct()
        )
        result = await self.db.execute(query)
        return result.all()


