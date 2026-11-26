"""상대 모멘텀 데이터 접근 — 순위 유니버스 일봉, 계좌 소유자·모의 여부, 모멘텀 보유 스윙

순수 DB 쿼리만 담당한다 (트랜잭션 관리 안 함).
"""
from datetime import datetime, timedelta
from typing import Collection, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import select, text, bindparam
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.swing.entity import SwingTrade

KR_MARKETS = ("J", "NX", "UN")
US_MARKETS = ("NYS", "NAS", "AMS")
MARKET_CODES = {"KR": KR_MARKETS, "US": US_MARKETS}
MOMENTUM_TYPE = "M"                 # SWING_TRADE.SWING_TYPE — 모멘텀 포지션 기록 행

# 순위에 넣지 않는 종목 — 비교용 지수 ETF (매매 대상이 아니다). 파킹 ETF 는 호출부가 따로 뺀다
EXCLUDED_CODES = {("J", "069500"), ("AMS", "SPY"), ("NAS", "QQQ")}


class MomentumRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def load_universe_bars(self, pairs: Collection[Tuple[str, str]], since: str) -> Dict[Tuple[str, str], pd.DataFrame]:
        """유니버스 종목들의 일봉 — pairs 는 [(시장코드, 종목코드)] (universe.universe_pairs), since 는 YYYYMMDD

        반환: (시장코드, 종목코드) → 날짜 오름차순 DataFrame (OHLCV 컬럼, 보정 전 원본). 보정은 ranking.clean_bars 가 한다.
        봉이 없는 종목은 결과에 없다. 비교용 지수 ETF 는 제외한다.
        """
        wanted = {p for p in pairs if p not in EXCLUDED_CODES}
        if not wanted:
            return {}
        query = text(
            "SELECT MRKT_CODE, ST_CODE, STCK_BSOP_DATE, STCK_OPRC, STCK_HGPR, STCK_LWPR, STCK_CLPR, ACML_VOL "
            "FROM STOCK_DAY_HISTORY "
            "WHERE MRKT_CODE IN :mrkts AND ST_CODE IN :codes AND STCK_BSOP_DATE >= :since "
            "ORDER BY MRKT_CODE, ST_CODE, STCK_BSOP_DATE"
        ).bindparams(bindparam("mrkts", expanding=True), bindparam("codes", expanding=True))
        rows = (await self.db.execute(query, {"mrkts": sorted({m for m, _ in wanted}),
                                              "codes": sorted({c for _, c in wanted}), "since": since})).all()
        if not rows:
            return {}
        df = pd.DataFrame(rows, columns=["MRKT_CODE", "ST_CODE", "STCK_BSOP_DATE", "STCK_OPRC", "STCK_HGPR",
                                         "STCK_LWPR", "STCK_CLPR", "ACML_VOL"])
        out = {}
        for (m, code), g in df.groupby(["MRKT_CODE", "ST_CODE"], sort=False):
            if (m, code) in wanted:
                out[(m, code)] = g.drop(columns=["MRKT_CODE", "ST_CODE"]).reset_index(drop=True)
        return out

    async def find_account_owner(self, account_no: str) -> Optional[Tuple[str, str]]:
        """계좌의 (USER_ID, SIMULATION_YN) — 같은 계좌번호가 여러 인증키로 등록된 경우 최신 등록분 (배치와 같은 규칙). 없으면 None"""
        query = text(
            "SELECT A.USER_ID, U.SIMULATION_YN FROM ACCOUNT A "
            "JOIN AUTH_KEY U ON A.USER_ID = U.USER_ID AND A.AUTH_ID = U.AUTH_ID "
            "WHERE A.ACCOUNT_NO = :no ORDER BY A.ACCOUNT_ID DESC LIMIT 1"
        )
        row = (await self.db.execute(query, {"no": account_no})).first()
        return (row[0], row[1]) if row else None

    async def find_momentum_swings(self, account_no: str, market: str) -> List[SwingTrade]:
        """계좌·시장의 모멘텀 스윙 행 전체 (보유 여부와 무관)"""
        q = select(SwingTrade).filter(
            SwingTrade.ACCOUNT_NO == account_no, SwingTrade.SWING_TYPE == MOMENTUM_TYPE,
            SwingTrade.MRKT_CODE.in_(MARKET_CODES[market]))
        return list((await self.db.execute(q)).scalars().all())

    async def find_swing(self, account_no: str, mrkt_code: str, st_code: str) -> Optional[SwingTrade]:
        """(계좌, 시장, 종목) 스윙 — 타입과 무관. 이미 다른 타입(S 등)으로 등록된 종목을 알아보는 데 쓴다"""
        q = select(SwingTrade).filter(
            SwingTrade.ACCOUNT_NO == account_no, SwingTrade.MRKT_CODE == mrkt_code, SwingTrade.ST_CODE == st_code)
        return (await self.db.execute(q)).scalar_one_or_none()

    async def save(self, swing: SwingTrade) -> SwingTrade:
        """저장 (flush만, commit 안 함)"""
        self.db.add(swing)
        await self.db.flush()
        return swing


def recent_since(days: int = 420) -> str:
    """순위 계산에 필요한 일봉 시작일(YYYYMMDD) — 126봉 + 여유"""
    return (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
