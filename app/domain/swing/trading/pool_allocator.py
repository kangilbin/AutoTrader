"""
풀 할당기 — 풀(계좌 × 시장) 하나의 매수 후보를 정렬·사이징·한도 확인 후 순차로 주문한다

배치가 각 스윙의 신호를 평가해 후보만 모은 뒤(주문 안 함), 청산이 끝난 시점에 이 모듈을 부른다.
한 건 체결할 때마다 풀 상태(현금·보유 수·열린 리스크)를 갱신해 같은 사이클에서 같은 돈을 두 번 쓰지 않는다.

실제 매수(실보유 확인·편입, 주문·체결 확인, 이력 저장, 스윙 상태 커밋)는 주입받는 buy 함수가 한다.
기존 매수 경로(_adopt_before_buy, execute_buy)가 auto_swing_batch 에 있어 직접 import 하면 순환 참조가 되고,
주입하면 이 모듈은 DB·KIS 없이 테스트할 수 있다.

한도는 신규 매수만 막는다 — 보유 포지션을 청산하는 용도로 쓰지 않는다.
"""
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, List, Tuple

from app.domain.pool.entity import PoolSetting
from app.domain.swing import risk_sizing
from app.domain.swing.trading.pool_state import AccountSnapshot, PoolPosition, compute_pool_state
from app.domain.swing.trading.strategies.base_single_ema import BaseSingleEMAStrategy

logger = logging.getLogger(__name__)

SKIP_NO_ATR = "no_atr"          # ATR 을 몰라 청산선·리스크를 계산할 수 없다 (실전 청산 판정도 ATR 0 이면 건너뛴다)
SKIP_ZERO_QTY = "zero_qty"      # 총자산이 작거나 손절폭이 없어 1주도 못 산다
SKIP_ABORTED = "aborted"        # 연속 주문 실패로 이번 사이클 중단

CASH_BUFFER = 0.01              # 현금 확인 시 주문 금액에 얹는 여유 — 시장가 체결이 현재가보다 높을 수 있다
COST_RATE = 0.003               # 체결 후 현금에서 빼는 수수료·세금 여유
MAX_CONSECUTIVE_FAILURES = 3    # 연속 실패가 이만큼이면 외부 장애로 보고 사이클 중단

# 매수 결과
BUY_FILLED = "filled"           # 체결 (일부 체결 포함 — 진입은 1회뿐이라 잔량을 다시 사지 않는다)
BUY_UNCONFIRMED = "unconfirmed" # 주문은 나갔으나 체결 미확인 — 체결됐을 수 있으니 계획대로 반영한다
BUY_ADOPTED = "adopted"         # 이미 보유 중이라 매수 대신 편입 (현금 변화 없음)
BUY_FAILED = "failed"           # 주문 실패·보류 (외부 장애일 수 있어 연속 실패로 센다)
BUY_SKIPPED = "skipped"         # 후보를 고른 뒤 스윙 상태가 바뀌어 사지 않음 (장애가 아니므로 실패로 세지 않는다)
BUY_HALT = "halt"               # 이 풀의 이번 사이클 매수를 모두 멈춘다 (파킹 ETF 해제 결과를 모르는 채 다음 후보가 같은 보유를 또 팔면 안 된다)


@dataclass(frozen=True)
class EntryCandidate:
    """매수 신호가 확정된 스윙 하나 (연속 확인까지 통과)"""
    swing_id: int
    user_id: str
    account_no: str
    st_code: str
    mrkt_code: str
    price: float                # 현재가 — 수량 계산 기준
    conviction: float           # 우선순위 (높을수록 먼저)
    atr: float                  # 전일 완성봉 ATR — 청산 판정과 같은 값
    avg_daily_amount: float     # 20일 평균 거래대금 (유동성 필터)
    reasons: Tuple[str, ...] = ("EMA돌파",)


@dataclass(frozen=True)
class BuyOutcome:
    status: str
    qty: int = 0                # 체결 수량 (BUY_ADOPTED 는 편입한 보유 수량)
    avg_price: float = 0.0


BuyFn = Callable[[EntryCandidate, int], Awaitable[BuyOutcome]]


@dataclass
class PoolRunResult:
    attempted: int = 0
    outcomes: List[Tuple[str, str, int]] = field(default_factory=list)   # (종목, 결과, 수량)
    skipped: Counter = field(default_factory=Counter)

    @property
    def filled(self) -> int:
        return sum(1 for _, s, _ in self.outcomes if s in (BUY_FILLED, BUY_UNCONFIRMED))


def initial_stop(price: float, atr: float) -> float:
    """진입 시 청산선 = 1R 기준 — 매수 직후 실전 청산 판정(calculate_exit_line)이 쓰는 값과 같다"""
    return BaseSingleEMAStrategy.calculate_exit_line(price, price, atr)


def order_candidates(candidates: Iterable[EntryCandidate]) -> List[EntryCandidate]:
    """conviction 높은 순 — 같으면 (시장, 종목코드) 순으로 정해 입력 순서에 기대지 않는다"""
    return sorted(candidates, key=lambda c: (-c.conviction, c.mrkt_code, c.st_code))


async def run_pool_entries(
    setting: PoolSetting,
    candidates: Iterable[EntryCandidate],
    snapshot: AccountSnapshot,
    positions: Iterable[PoolPosition],
    buy: BuyFn,
) -> PoolRunResult:
    """풀 하나의 후보를 순서대로 사이징·한도 확인 후 매수한다

    Args:
        setting: 풀 설정 (리스크·종목당 상한·종목 수·총 리스크 한도)
        candidates: 이번 사이클의 매수 후보
        snapshot: 청산이 끝난 뒤 읽은 계좌 총자산·가용 현금
        positions: 청산 후 DB 에 남은 관리 중 보유 포지션
        buy: 실제 매수 함수 (후보, 수량) → BuyOutcome
    """
    state = compute_pool_state(snapshot, positions)
    equity, cash = state.equity, state.cash
    count, open_risk = state.position_count, state.open_risk
    risk_pct = float(setting.RISK_PCT)
    max_position_pct = float(setting.MAX_POSITION_PCT)
    max_open_risk_pct = float(setting.MAX_OPEN_RISK_PCT)

    result = PoolRunResult()
    consecutive_failures = 0
    ordered = order_candidates(candidates)

    for i, c in enumerate(ordered):
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            result.skipped[SKIP_ABORTED] += len(ordered) - i
            logger.error(f"[POOL] 연속 {consecutive_failures}건 주문 실패 → 이번 사이클 중단 (남은 후보 {len(ordered) - i}건)")
            break

        if c.atr <= 0:
            result.skipped[SKIP_NO_ATR] += 1
            logger.warning(f"[{c.st_code}] ATR 없음 → 풀 매수 보류 (청산선·리스크 계산 불가)")
            continue

        stop = initial_stop(c.price, c.atr)
        sized = risk_sizing.calc_qty(equity, risk_pct, c.price, stop, max_position_pct)
        if sized.qty <= 0:
            result.skipped[SKIP_ZERO_QTY] += 1
            continue

        amount = sized.qty * c.price
        if not risk_sizing.within_liquidity(amount, c.avg_daily_amount, BaseSingleEMAStrategy.MAX_ADV_RATIO):
            result.skipped[risk_sizing.REJECT_LIQUIDITY] += 1
            logger.info(f"[{c.st_code}] 유동성 부족 → 풀 매수 보류 (금액 {amount:,.0f} / 일평균 거래대금 {c.avg_daily_amount:,.0f})")
            continue

        reason = risk_sizing.check_limits(
            order_amount=amount * (1 + CASH_BUFFER), new_risk=sized.risk_amount, cash=cash, equity=equity,
            position_count=count, open_risk=open_risk,
            max_positions=setting.MAX_POSITIONS, max_open_risk_pct=max_open_risk_pct,
        )
        if reason:
            result.skipped[reason] += 1
            logger.info(f"[{c.st_code}] 풀 한도로 매수 보류: {reason} "
                        f"(현금 {cash:,.0f}, 보유 {count}종목, 열린 리스크 {open_risk / equity:.2%})")
            continue

        logger.info(f"[{c.st_code}] 풀 매수 시도: {sized.qty}주 @ {c.price:,.2f} "
                    f"(리스크 {sized.risk_amount:,.0f} = 총자산의 {sized.risk_amount / equity:.3%}, 제약 {sized.binding})")
        result.attempted += 1
        outcome = await buy(c, sized.qty)
        result.outcomes.append((c.st_code, outcome.status, outcome.qty))

        if outcome.status == BUY_HALT:
            result.skipped[SKIP_ABORTED] += len(ordered) - i - 1
            logger.error(f"[POOL] 매수 중단 요청 → 이번 사이클 남은 후보 {len(ordered) - i - 1}건 보류")
            break
        if outcome.status == BUY_SKIPPED:
            continue
        if outcome.status == BUY_FAILED:
            consecutive_failures += 1
            continue
        consecutive_failures = 0

        if outcome.status == BUY_FILLED:
            qty, price = outcome.qty, outcome.avg_price or c.price
            cash -= qty * price * (1 + COST_RATE)
            count += 1
            open_risk += risk_sizing.position_open_risk(price, stop, qty)
        elif outcome.status == BUY_UNCONFIRMED:
            # 체결됐을 수 있다 — 계획대로 반영해 뒤 후보가 같은 돈·같은 위험 한도를 다시 쓰지 않게 한다
            cash -= amount * (1 + COST_RATE)
            count += 1
            open_risk += sized.risk_amount
        elif outcome.status == BUY_ADOPTED:
            count += 1       # 이미 계좌에 있던 보유라 현금은 이미 빠져 있다
            open_risk += risk_sizing.position_open_risk(outcome.avg_price or c.price, stop, outcome.qty)

        cash = max(cash, 0.0)

    return result
