"""
풀 상태 — 풀 매수(총자산 기준 리스크 사이징)에 필요한 계좌 상태

세 부분으로 나뉜다. 외부 호출·DB 읽기(fetch_account_snapshot, load_positions)와 계산(compute_pool_state)을
분리해, 계산은 DB·KIS 없이 테스트한다.

현금(cash)은 '지금 실제로 주문할 수 있는 돈'이다 — 잔고의 예수금과 **매수가능조회 API** 중 작은 값:
- 국내: min(prvs_rcdl_excc_amt(D+2 예수금), 매수가능조회 nrcvb_buy_amt(미수없는매수금액, 매도대금 재사용분 포함)).
  모의 실측: 예수금 92,655,313 / 미수없는매수금액 92,642,163 / (참고) ord_psbl_cash 92,192,036.
- 미국: min(외화예수금 dnca_amt, 매수가능금액조회 주문가능외화). 모의 실측에서 예수금 $240,095 vs 주문가능 $86,212 로 크게 달랐다 —
  예수금으로 계획하면 주문이 거절된다. 통합증거금으로 원화가 환전돼 쓰이는 매수력은 반영하지 않는다 (보수적).
총자산(equity)은 예수금 + 보유 평가금이다 (tot_evlu_amt / 외화예수금 + 보유 평가금 합).
매수가능조회가 실패하면 예수금으로 대신하되(계획용), strict 호출(돈이 움직이는 판단)은 None 으로 막는다.

가정 (모의투자 실측으로 확인 필요 — docs/01-plan/features/portfolio-risk-sizing.checklist.md 4-8):
- 파킹 ETF 를 판 직후 매수가능조회 값이 늘어난다 (미국은 sll_ruse_psbl_amt 가 매도대금 재사용분을 알려준다).
"""
import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.pool.entity import PARK_ETF, POOL_KR, POOL_US
from app.domain.swing import risk_sizing
from app.domain.swing.repository import SwingRepository
from app.domain.swing.trading.strategies.base_single_ema import BaseSingleEMAStrategy
from app.domain.swing.trading.strategies.single_ema_strategy import SingleEMAStrategy
from app.external import foreign_api, kis_api

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountSnapshot:
    """계좌 한 시장의 금액 (국내 원, 미국 달러)"""
    market: str
    equity: float   # 총자산 = 현금 + 보유 평가금 (사용자가 직접 산 종목·파킹 상품 포함)
    cash: float     # 신규 매수에 쓸 수 있는 현금


@dataclass(frozen=True)
class PoolPosition:
    """보유 포지션 하나 — 열린 리스크 계산 입력"""
    code: str
    qty: int
    entry_price: float
    peak_price: float
    atr: float      # 전일 완성봉 ATR (없으면 0 → 최대 손절폭으로 보수적 계산)

    @property
    def exit_line(self) -> float:
        """현재 청산선 — 실전 청산 판정과 같은 함수 (진입 시 청산선이 1R 기준)"""
        return BaseSingleEMAStrategy.calculate_exit_line(self.entry_price, self.peak_price, self.atr)

    @property
    def open_risk(self) -> float:
        return risk_sizing.position_open_risk(self.entry_price, self.exit_line, self.qty)


@dataclass(frozen=True)
class PoolState:
    equity: float
    cash: float
    position_count: int
    open_risk: float

    @property
    def open_risk_pct(self) -> float:
        return self.open_risk / self.equity if self.equity > 0 else 0.0


def _num(value) -> float:
    """KIS 응답 문자열 → float (없거나 깨진 값은 0)"""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def compute_pool_state(snapshot: AccountSnapshot, positions: Iterable[PoolPosition]) -> PoolState:
    """계좌 스냅샷 + 보유 포지션 → 풀 상태 (순수 계산)"""
    positions = list(positions)
    return PoolState(
        equity=snapshot.equity,
        cash=snapshot.cash,
        position_count=len(positions),
        open_risk=sum(p.open_risk for p in positions),
    )


async def _fetch_buying_power(user_id: str, db: AsyncSession, account_no: str, market: str) -> Optional[float]:
    """매수가능조회로 읽은 주문가능 현금 — 알 수 없으면 None (금액은 종목과 무관해 파킹 ETF 코드를 대표로 쓴다)"""
    etf = PARK_ETF[market]
    if market == POOL_KR:
        bp = await kis_api.get_buying_power(user_id, db, etf["code"], account_no=account_no)
    else:
        bp = await foreign_api.get_buying_power(user_id, db, etf["code"], etf["excg"], 100.0, account_no=account_no)
    return None if bp is None else bp["cash"]


async def fetch_account_snapshot(
    user_id: str, db: AsyncSession, account_no: str, market: str,
    *, with_buying_power: bool = True, strict: bool = False,
) -> Optional[AccountSnapshot]:
    """KIS 잔고로 총자산·가용 현금을 읽는다 — 알 수 없으면 None (호출부는 이번 사이클 풀 매수를 건너뛴다)

    총자산이 0 이하이거나 읽을 수 없는 값으로 사이징하면 수량이 0이거나 엉뚱해지므로 None 으로 막는다.

    with_buying_power: 현금을 예수금과 매수가능조회 중 작은 값으로 한다 (False 면 총자산만 필요한 호출 — 조회 1회 절약).
    strict: 매수가능조회가 실패하면 예수금으로 대신하지 않고 None — 파킹 ETF 해제 직후 확인처럼 돈이 움직이는 판단용
    (계획용 호출은 예수금으로 대신해도 주문 단계에서 증권사가 걸러낸다).
    """
    if market == POOL_KR:
        balance = await kis_api.get_stock_balance(user_id, db, account_no=account_no)
        o2 = balance.get("output2") or {}
        holdings_value = _num(o2.get("scts_evlu_amt"))
        # D+2 예수금이 없으면(필드 누락) 예수금 총금액으로 대체 — 매도대금 재사용분이 빠져 보수적이다
        cash = _num(o2.get("prvs_rcdl_excc_amt")) if o2.get("prvs_rcdl_excc_amt") not in (None, "") \
            else _num(o2.get("dnca_tot_amt"))
        equity = _num(o2.get("tot_evlu_amt")) or (cash + holdings_value)
    elif market == POOL_US:
        present = await foreign_api.get_present_balance(user_id, db, account_no=account_no)
        if present is None:   # 모의 환율 미수신 등 — 현금을 산출할 수 없다
            logger.warning(f"[POOL] 미국 예수금 산출 불가 → 풀 매수 보류 (계좌 {account_no[-4:]})")
            return None
        cash = _num(present.get("dnca_amt"))
        holdings = await foreign_api.get_stock_balance(user_id, db, excg_cd="NASD", account_no=account_no)
        equity = cash + sum(_num(item.get("evlu_amt")) for item in holdings.get("output1", []))
    else:
        raise ValueError(f"알 수 없는 풀 시장: {market}")

    if equity <= 0:
        logger.warning(f"[POOL] {market} 총자산을 읽을 수 없음 (equity={equity}) → 풀 매수 보류")
        return None
    if with_buying_power:
        orderable = await _fetch_buying_power(user_id, db, account_no, market)
        if orderable is not None:
            cash = min(cash, orderable)
        elif strict:
            logger.warning(f"[POOL] {market} 매수가능조회 실패 → 주문가능 금액을 확정할 수 없어 보류")
            return None
    return AccountSnapshot(market=market, equity=equity, cash=max(0.0, min(cash, equity)))


async def load_positions(
    repo: SwingRepository, redis_client, account_no: str, market: str
) -> List[PoolPosition]:
    """DB 보유 스윙 + 지표 캐시의 ATR → 포지션 목록

    지표 캐시가 없으면 ATR 0 으로 둔다 — calculate_exit_line 이 최대 손절폭(진입가 −7%)으로 계산해
    열린 리스크가 과소 평가되지 않는다.
    """
    swings = await repo.find_holdings(account_no, overseas=(market == POOL_US))
    positions = []
    for s in swings:
        cached = await SingleEMAStrategy.get_cached_indicators(redis_client, s.ST_CODE)
        atr = float(cached["atr"]) if cached and cached.get("atr") else 0.0
        entry = float(s.ENTRY_PRICE or 0)
        positions.append(PoolPosition(
            code=s.ST_CODE, qty=int(s.HOLD_QTY or 0), entry_price=entry,
            peak_price=float(s.PEAK_PRICE or entry), atr=atr,
        ))
    return positions
