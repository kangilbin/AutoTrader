"""상대 모멘텀 월간 리밸런싱 배치 — 순위 → 이탈 종목 매도 → 신규 편입 매수

핵심 흐름 (MomentumRebalancer.run, 하루에 여러 번 불려도 한 달에 한 번만 일한다):
  1. 체결 미확인 주문 정리 + KIS 실보유와 DB 행 대조 (진실은 KIS 잔고다)
  2. 그 달 계획이 없으면 만든다: 유니버스 일봉 → 보정 → 순위 → 보유 vs 상위 10 (ranking.plan_rebalance). 계획은 Redis 에 저장해 이어서 처리한다
  3. 이탈 종목 전량 매도
  4. 신규 편입 매수 — 총자산의 9.5%, 현금이 모자라면 살 수 있는 만큼(목표의 50% 이상), 못 사는 종목은 차순위로 대체
  5. 매수가 필요한 만큼 끝났거나 후보가 바닥나면 그 달을 끝낸다 (일시적 실패는 최대 CUTOFF_DAYS 일 동안 다음 실행에서 재시도)

안전장치: 설정에서 꺼져 있으면 아무것도 안 하고, **모의투자 계좌(SIMULATION_YN='Y')만** 허용한다 (실계좌는 어떤 경우에도 거부).
주문·체결 기록은 기존 SwingOrderExecutor 를 그대로 쓴다 (TRADE_HISTORY 단일 저장 지점 유지).
포지션 기록은 SWING_TRADE 의 SWING_TYPE='M' 행이다 — 신호 기반 배치·풀 계산은 이 타입을 보지 않는다 (swing/repository.py).
"""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Awaitable, Callable, Dict, List, Optional, Protocol, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from app.domain.momentum import ranking as rk
from app.domain.swing.entity import MOMENTUM_TYPE, SwingTrade

logger = logging.getLogger(__name__)

STATE_TTL = 45 * 86400        # 월 상태 Redis 보존 (초)
CUTOFF_DAYS = 7               # 계획을 만든 뒤 이 일수가 지나면 남은 항목을 포기하고 그 달을 끝낸다
ADV_WINDOW = 20               # 평균 거래대금 창 (실전 지표 캐시와 같은 20일)
STALE_DAYS = 10               # 시장의 마지막 봉보다 이만큼 오래된 종목은 거래 불가(폐지·정지)로 본다
HISTORY_DAYS = 420            # 순위에 필요한 일봉 조회 기간 (126봉 + 여유)

# 브로커 결과 상태
FILLED, PARTIAL, UNCONFIRMED, FAILED = "filled", "partial", "unconfirmed", "failed"
# ensure_cash 결과
CASH_OK, CASH_INSUFFICIENT, CASH_HALT, CASH_FAILED = "ok", "insufficient", "halt", "failed"


# ==================== 브로커 인터페이스 ====================

@dataclass
class OrderResult:
    status: str                      # FILLED | PARTIAL(매도만) | UNCONFIRMED | FAILED
    qty: int = 0                     # 체결 수량
    avg_price: float = 0.0
    amount: float = 0.0              # 체결 금액
    reason: str = ""
    pending: Optional[dict] = None   # UNCONFIRMED 일 때 다음 실행이 재확인할 주문 정보


@dataclass
class PendingResolution:
    completed: bool = False
    hold_qty: Optional[int] = None   # 확인된 뒤의 보유 수량 (모르면 None)
    entry_price: Optional[float] = None
    pending_state: Optional[dict] = None   # 아직 미확인이면 다음 실행에 다시 재확인


class Broker(Protocol):
    """주문·잔고·시세 접근 — 실전은 KisBroker, 테스트는 가짜 구현"""
    async def price(self, mrkt_code: str, code: str) -> Optional[float]: ...
    async def holdings(self) -> Optional[Dict[str, int]]: ...                      # 종목코드 → 보유 수량, 조회 실패면 None
    async def snapshot(self) -> Optional[Tuple[float, float]]: ...                 # (총자산, 매수 가능 금액(파킹 평가금 포함))
    async def ensure_cash(self, needed: float) -> str: ...                         # 파킹 ETF 해제 등으로 현금 확보
    async def buy(self, swing: SwingTrade, price: float, qty: int, reasons: List[str]) -> OrderResult: ...
    async def sell(self, swing: SwingTrade, price: float, qty: int, reasons: List[str]) -> OrderResult: ...
    async def resolve_pending(self, swing: SwingTrade, price: float, pending: dict) -> PendingResolution: ...


# ==================== 순위 입력 ====================

@dataclass
class UniverseSnapshot:
    closes: Dict[str, List[float]]   # 종목코드 → 보정된 종가열(오름차순, 마지막=최근 완성봉)
    adv: Dict[str, float]            # 종목코드 → 20일 평균 거래대금 (유동성 한도용)
    mrkt: Dict[str, str]             # 종목코드 → 시장코드 (J/NYS/NAS/AMS)
    latest: str                      # 시장의 마지막 봉 날짜 (YYYYMMDD)


def build_universe_snapshot(bars: Dict[Tuple[str, str], pd.DataFrame], today: str,
                            park_codes: Tuple[str, ...] = ()) -> Optional[UniverseSnapshot]:
    """DB 일봉 → 순위 입력. 오늘(미완성) 봉은 빼고, 시장의 마지막 봉보다 너무 오래된 종목(거래 불가)과 파킹 ETF 는 뺀다"""
    closes, adv, mrkt, last_dates = {}, {}, {}, {}
    for (m, code), df in bars.items():
        if code in park_codes or df is None or df.empty:
            continue
        df = df[df["STCK_BSOP_DATE"] < today]          # 완성된 봉만 (오늘 봉은 아직 미완성 — 연구도 전일까지만 썼다)
        if df.empty:
            continue
        cleaned = rk.clean_bars(df)
        closes[code] = [float(x) for x in cleaned["STCK_CLPR"]]
        amount = (cleaned["STCK_CLPR"].astype(float) * cleaned["ACML_VOL"].astype(float)).tail(ADV_WINDOW).mean()
        adv[code] = float(amount) if pd.notna(amount) else 0.0
        mrkt[code] = m
        last_dates[code] = df["STCK_BSOP_DATE"].iloc[-1]
    if not last_dates:
        return None
    latest = max(last_dates.values())
    fresh = [c for c, d in last_dates.items() if (pd.Timestamp(latest) - pd.Timestamp(d)).days <= STALE_DAYS]
    return UniverseSnapshot({c: closes[c] for c in fresh}, {c: adv[c] for c in fresh},
                            {c: mrkt[c] for c in fresh}, latest)


# ==================== 리밸런서 ====================

class MomentumRebalancer:
    """한 계좌·시장의 한 달치 리밸런싱 — 외부 의존(DB·Redis·브로커·유니버스)은 모두 주입받는다 (테스트 가능)"""

    def __init__(self, *, account_no: str, market: str, month: str, repo, db, redis,
                 broker: Broker, load_universe: Callable[[], Awaitable[Optional[UniverseSnapshot]]],
                 mrkt_code: str, now: Optional[datetime] = None,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep, order_spacing_sec: float = 2.0,
                 top_n: int = rk.TOP_N):
        self.account_no, self.market, self.month = account_no, market, month
        self.repo, self.db, self.redis, self.broker = repo, db, redis, broker
        self.load_universe, self.mrkt_code = load_universe, mrkt_code
        self.now = now or datetime.now()
        self.sleep, self.spacing, self.top_n = sleep, order_spacing_sec, top_n
        self.summary: Dict = {"orders": [], "skipped": {}}

    # ---------- Redis 상태 ----------

    @property
    def _state_key(self) -> str:
        return f"momentum:state:{self.account_no}:{self.market}:{self.month}"

    async def _load_state(self) -> Optional[dict]:
        raw = await self.redis.get(self._state_key)
        return json.loads(raw) if raw else None

    async def _save_state(self, state: dict) -> None:
        await self.redis.setex(self._state_key, STATE_TTL, json.dumps(state, ensure_ascii=False))

    # ---------- 실행 ----------

    async def run(self) -> Dict:
        state = await self._load_state()
        if state and state.get("status") == "done":
            # 그 달 리밸런싱은 끝났어도 체결 미확인 주문은 계속 정리한다 (미루면 다음 달까지 DB 와 계좌가 어긋난다)
            await self._resolve_pendings()
            self.summary["status"] = "already_done"
            return self.summary

        await self._resolve_pendings()
        if not await self._reconcile():
            self.summary["status"] = "no_holdings"          # 잔고를 못 읽으면 아무것도 하지 않는다 (다음 실행에 재시도)
            return self.summary

        if state is None:
            state = await self._make_plan()
            if state is None:
                self.summary["status"] = "no_ranking"       # 유니버스 부족·데이터 없음 — 상태를 남기지 않아 다음 실행에 다시 본다
                return self.summary

        await self._execute_sells(state)
        await self._execute_buys(state)
        await self._finish_if_done(state)
        self.summary["status"] = state["status"]
        return self.summary

    # ---------- 1. 미확인 주문 정리 · 실보유 대조 ----------

    async def _resolve_pendings(self) -> None:
        for swing in await self.repo.find_momentum_swings(self.account_no, self.market):
            key = f"pending_order:{swing.SWING_ID}"
            raw = await self.redis.get(key)
            if not raw:
                continue
            price = await self.broker.price(swing.MRKT_CODE, swing.ST_CODE) or float(swing.ENTRY_PRICE or 0) or 1.0
            res = await self.broker.resolve_pending(swing, price, json.loads(raw))
            if res.hold_qty is not None:
                self._apply_holding(swing, res.hold_qty, res.entry_price or float(swing.ENTRY_PRICE or 0) or price)
            if res.pending_state:
                await self.redis.setex(key, 86400, json.dumps(res.pending_state))
            else:
                await self.redis.delete(key)
            await self.db.commit()

    @staticmethod
    def _apply_holding(swing: SwingTrade, qty: int, entry_price: float) -> None:
        if qty > 0:
            swing.enter_momentum(entry_price, qty)
        else:
            swing.exit_momentum()

    async def _reconcile(self) -> bool:
        """DB 의 모멘텀 행을 KIS 잔고에 맞춘다. 잔고를 읽을 수 없으면 False. 모멘텀 행이 없는 보유(직접 산 종목)는 건드리지 않는다"""
        holdings = await self.broker.holdings()
        if holdings is None:
            logger.warning(f"[MOMENTUM {self.market}] 잔고 조회 실패 → 이번 실행 보류")
            return False
        changed = False
        for swing in await self.repo.find_momentum_swings(self.account_no, self.market):
            if await self.redis.get(f"pending_order:{swing.SWING_ID}"):
                continue                                    # 미확인 주문이 남은 종목은 확인이 끝나기 전에 대조하지 않는다
            kis_qty = int(holdings.get(swing.ST_CODE, 0))
            if kis_qty == int(swing.HOLD_QTY or 0):
                continue
            price = float(swing.ENTRY_PRICE or 0) or await self.broker.price(swing.MRKT_CODE, swing.ST_CODE) or 0.0
            if kis_qty > 0 and price <= 0:
                continue
            logger.warning(f"[MOMENTUM {self.market}] {swing.ST_CODE} 실보유 대조: DB {swing.HOLD_QTY or 0}주 → KIS {kis_qty}주")
            self._apply_holding(swing, kis_qty, price)
            self.summary.setdefault("reconciled", []).append(swing.ST_CODE)
            changed = True
        if changed:
            await self.db.commit()
        return True

    # ---------- 2. 계획 ----------

    async def _make_plan(self) -> Optional[dict]:
        snap = await self.load_universe()
        if snap is None:
            logger.warning(f"[MOMENTUM {self.market}] 순위 입력 없음 → 이번 달 변경 없음 (다음 실행에 재시도)")
            return None
        ranked = rk.rank_universe(snap.closes)
        if not ranked:
            logger.warning(f"[MOMENTUM {self.market}] 점수 보유 종목 {len(snap.closes)}개 < {rk.MIN_UNIVERSE} → 이번 달 변경 없음")
            return None
        held = [s.ST_CODE for s in await self.repo.find_momentum_swings(self.account_no, self.market)
                if int(s.HOLD_QTY or 0) > 0]
        plan = rk.plan_rebalance(held, ranked, self.top_n)
        info = {r.code: {"rank": r.rank, "score": round(r.score, 4)} for r in ranked[:self.top_n + rk.BACKFILL_DEPTH]}
        state = {"status": "running", "created": self.now.isoformat(), "as_of": snap.latest,
                 "targets": plan.target, "sells": plan.sells, "buys": plan.buys, "backfill": plan.backfill,
                 "info": info, "adv": {c: snap.adv.get(c, 0.0) for c in plan.buys + plan.backfill},
                 "mrkt": {c: snap.mrkt[c] for c in plan.buys + plan.backfill + plan.sells if c in snap.mrkt},
                 "sell_done": [], "buy_done": [], "buy_failed": {}}
        await self._save_state(state)
        logger.info(f"[MOMENTUM {self.market}] 계획 {self.month} (기준 봉 {snap.latest}): 상위 {plan.target} · "
                    f"매도 {plan.sells} · 매수 {plan.buys} · 대체 후보 {plan.backfill[:5]}")
        return state

    # ---------- 3. 매도 ----------

    async def _execute_sells(self, state: dict) -> None:
        swings = {s.ST_CODE: s for s in await self.repo.find_momentum_swings(self.account_no, self.market)}
        for code in state["sells"]:
            if code in state["sell_done"]:
                continue
            swing = swings.get(code)
            if swing is None or int(swing.HOLD_QTY or 0) <= 0:
                state["sell_done"].append(code)             # 이미 없다 (대조로 정리됨)
                continue
            if await self.redis.get(f"pending_order:{swing.SWING_ID}"):
                continue                                    # 미확인 주문이 남아 있으면 새 주문을 내지 않는다
            price = await self.broker.price(swing.MRKT_CODE, code)
            if not price:
                logger.warning(f"[MOMENTUM {self.market}] {code} 현재가 조회 실패 → 매도 보류 (다음 실행)")
                continue
            qty = int(swing.HOLD_QTY)
            info = state["info"].get(code)
            reasons = ["모멘텀 이탈", f"상위 {self.top_n}위 밖" + (f" ({info['rank']}위)" if info else "")]
            res = await self.broker.sell(swing, price, qty, reasons)
            self.summary["orders"].append(("sell", code, res.status, res.qty))
            await self._apply_sell(swing, state, code, qty, res)
            await self.sleep(self.spacing)
        await self._save_state(state)

    async def _apply_sell(self, swing: SwingTrade, state: dict, code: str, qty: int, res: OrderResult) -> None:
        if res.status == FILLED and res.qty >= qty:
            swing.exit_momentum()
            state["sell_done"].append(code)
        elif res.status in (FILLED, PARTIAL) and res.qty > 0:
            swing.reduce_momentum(qty - res.qty)            # 남은 수량은 다음 실행에서 다시 판다
        elif res.status == UNCONFIRMED:
            await self.redis.setex(f"pending_order:{swing.SWING_ID}", 86400, json.dumps(res.pending))
        else:
            logger.error(f"[MOMENTUM {self.market}] {code} 매도 실패: {res.reason}")
        await self.db.commit()

    # ---------- 4. 매수 ----------

    async def _execute_buys(self, state: dict) -> None:
        held_count = sum(1 for s in await self.repo.find_momentum_swings(self.account_no, self.market)
                         if int(s.HOLD_QTY or 0) > 0)
        pending_sells = [c for c in state["sells"] if c not in state["sell_done"]]
        if pending_sells:
            self.summary["skipped"]["buy"] = "매도 미완료"      # 매도대금이 들어와야 현금이 생긴다 — 매도가 끝난 실행에서 산다
            return
        need = self.top_n - held_count
        if need <= 0:
            return
        snap = await self.broker.snapshot()
        if snap is None:
            logger.warning(f"[MOMENTUM {self.market}] 총자산·현금 조회 실패 → 매수 보류 (다음 실행)")
            self.summary["skipped"]["buy"] = "스냅샷 실패"
            return
        equity, cash = snap
        # 1순위 후보(상위 10 중 아직 없는 종목)를 순위순으로 산다. 일시적 실패(현금 부족·주문 거절)는 건드리지 않고 다음 실행에 다시 시도한다.
        for code in [c for c in state["buys"] if c not in state["buy_done"] and c not in state["buy_failed"]]:
            if need <= 0:
                break
            stop = await self._try_buy(state, code, equity, cash, need)
            if stop is None:                                # 중단 신호(파킹 해제 결과 불명 등)
                await self._save_state(state)
                return
            cash, need = stop
        # 차순위 대체는 '영구적으로 살 수 없는' 종목(다른 타입으로 등록·1주도 못 삼 등)을 메우는 데만 쓴다.
        for code in [c for c in state["backfill"] if c not in state["buy_done"] and c not in state["buy_failed"]]:
            if need <= 0 or self._backfill_slots(state) <= 0:
                break
            stop = await self._try_buy(state, code, equity, cash, need)
            if stop is None:
                break
            cash, need = stop
        await self._save_state(state)

    @staticmethod
    def _backfill_slots(state: dict) -> int:
        """차순위로 메워도 되는 자리 수 = 영구 실패한 후보 수 − 이미 차순위로 산 수"""
        failed = sum(1 for c in state["buys"] + state["backfill"] if c in state["buy_failed"])
        filled = sum(1 for c in state["backfill"] if c in state["buy_done"])
        return failed - filled

    async def _try_buy(self, state: dict, code: str, equity: float, cash: float, need: int) -> Optional[Tuple[float, int]]:
        """후보 하나를 산다. 반환: (남은 현금, 남은 필요 종목 수) 또는 None(이번 실행 중단)"""
        mrkt = state["mrkt"].get(code, self.mrkt_code)
        existing = await self.repo.find_swing(self.account_no, mrkt, code)
        if existing is not None and existing.SWING_TYPE != MOMENTUM_TYPE:
            state["buy_failed"][code] = f"다른 타입({existing.SWING_TYPE})으로 이미 등록된 종목"
            logger.warning(f"[MOMENTUM {self.market}] {code} → {state['buy_failed'][code]}, 차순위로 대체")
            return cash, need
        if existing is not None and int(existing.HOLD_QTY or 0) > 0:
            state["buy_done"].append(code)                  # 이미 보유 (대조로 반영됨) — 후보 소진
            return cash, need - 1
        price = await self.broker.price(mrkt, code)
        if not price:
            state["buy_failed"][code] = "현재가 조회 실패"
            return cash, need
        adv = state["adv"].get(code)
        if rk.size_order(equity, 1e18, price, adv) == 0:    # 현금이 무한이어도 0 이면 구조적으로 살 수 없다 (1주 가격 > 목표 금액)
            state["buy_failed"][code] = "목표 금액으로 1주도 살 수 없음"
            return cash, need
        qty = rk.size_order(equity, cash, price, adv)
        if qty <= 0:
            self.summary["skipped"][code] = "현금 부족"       # 일시적 — 기록하지 않아 다음 실행에 다시 본다
            return cash, need
        ens = await self.broker.ensure_cash(qty * price * (1 + rk.COST_BUFFER))
        if ens == CASH_HALT:
            return None
        if ens != CASH_OK:
            self.summary["skipped"][code] = f"현금 확보 실패({ens})"
            return cash, need
        swing = existing
        if swing is None:
            swing = SwingTrade.create(self.account_no, mrkt, code, MOMENTUM_TYPE)
            swing.USE_YN = 'Y'
            await self.repo.save(swing)
            await self.db.commit()                          # 주문 전에 SWING_ID 를 확정한다 (체결 이력이 SWING_ID 를 쓴다)
        info = state["info"].get(code, {})
        reasons = ["모멘텀 편입", f"6-1 {info.get('score', 0):+.1%}" if info else "6-1", f"{info.get('rank', '?')}위"]
        res = await self.broker.buy(swing, price, qty, reasons)
        self.summary["orders"].append(("buy", code, res.status, res.qty))
        if res.status == FILLED and res.qty > 0:
            swing.enter_momentum(res.avg_price or price, res.qty)
            state["buy_done"].append(code)
            await self.db.commit()
            await self.sleep(self.spacing)
            return max(cash - (res.amount or res.qty * price), 0.0), need - 1
        if res.status == UNCONFIRMED:
            await self.redis.setex(f"pending_order:{swing.SWING_ID}", 86400, json.dumps(res.pending))
            await self.db.commit()
            state["buy_done"].append(code)                  # 주문은 나갔다 — 다음 실행의 미확인 정리가 결과를 반영한다
            await self.sleep(self.spacing)
            return max(cash - qty * price, 0.0), need - 1
        logger.error(f"[MOMENTUM {self.market}] {code} 매수 실패: {res.reason}")
        self.summary["skipped"][code] = f"매수 실패: {res.reason}"
        await self.db.rollback()
        await self.sleep(self.spacing)
        return cash, need

    # ---------- 5. 종료 ----------

    async def _finish_if_done(self, state: dict) -> None:
        held_count = sum(1 for s in await self.repo.find_momentum_swings(self.account_no, self.market)
                         if int(s.HOLD_QTY or 0) > 0)
        pending_sells = [c for c in state["sells"] if c not in state["sell_done"]]
        untried_primary = [c for c in state["buys"] if c not in state["buy_done"] and c not in state["buy_failed"]]
        backfill_possible = self._backfill_slots(state) > 0 and any(
            c not in state["buy_done"] and c not in state["buy_failed"] for c in state["backfill"])
        age_days = (self.now - datetime.fromisoformat(state["created"])).days
        if not pending_sells and (held_count >= self.top_n or (not untried_primary and not backfill_possible)):
            state["status"] = "done"
        elif age_days >= CUTOFF_DAYS:
            state["status"] = "done"
            logger.warning(f"[MOMENTUM {self.market}] {self.month} 계획 {age_days}일 경과 → 남은 항목 포기 "
                           f"(매도 대기 {pending_sells}, 보유 {held_count}/{self.top_n})")
        await self._save_state(state)


# ==================== 잡 진입점 ====================

def _market_config(market: str) -> Tuple[str, str]:
    """(대표 시장코드, 타임존)"""
    return ("J", "Asia/Seoul") if market == "KR" else ("NAS", "America/New_York")


def _parse_accounts(raw: str) -> List[str]:
    return [a.strip() for a in (raw or "").split(",") if a.strip()]


async def momentum_rebalance_job(market: str) -> None:
    """스케줄러 진입점 — 정규장 시작 10분 뒤부터 하루 한 번 이상 불려도 한 달에 한 번만 일한다"""
    from app.common.database import Database
    from app.common.redis import Redis
    from app.core.config import get_settings
    from app.domain.swing.trading.auto_swing_batch import is_market_open, is_opening_guard

    settings = get_settings()
    if not settings.MOMENTUM_ENABLED:
        return
    accounts = _parse_accounts(settings.MOMENTUM_ACCOUNT_NOS)
    if not accounts:
        logger.warning("[MOMENTUM] MOMENTUM_ENABLED=true 이지만 MOMENTUM_ACCOUNT_NOS 가 비어 있다 → 실행 안 함")
        return
    mrkt_code, tz = _market_config(market)
    if not (is_market_open(mrkt_code) or settings.ALLOW_OFFHOURS_TRADING) or is_opening_guard(mrkt_code, 10):
        return
    month = datetime.now(ZoneInfo(tz)).strftime("%Y%m")
    redis = await Redis.get_connection()
    for account_no in accounts:
        lock_key = f"momentum:lock:{account_no}:{market}"
        if not await redis.set(lock_key, "1", nx=True, ex=1800):
            logger.warning(f"[MOMENTUM {market}] 계좌 {account_no[-4:]} 이미 실행 중 → 건너뜀")
            continue
        db = await Database.get_session()
        try:
            await _run_account(db, redis, account_no, market, month, mrkt_code, settings)
        except Exception as e:
            logger.error(f"[MOMENTUM {market}] 계좌 {account_no[-4:]} 리밸런싱 실패: {e}", exc_info=True)
            await db.rollback()
        finally:
            await db.close()
            await redis.delete(lock_key)


async def _run_account(db, redis, account_no: str, market: str, month: str, mrkt_code: str, settings) -> Optional[Dict]:
    from app.domain.momentum.repository import MomentumRepository, recent_since
    from app.domain.momentum.universe import universe_pairs
    from app.domain.momentum.kis_broker import KisBroker
    from app.domain.pool.entity import PARK_ETF

    repo = MomentumRepository(db)
    owner = await repo.find_account_owner(account_no)
    if owner is None:
        logger.error(f"[MOMENTUM {market}] 계좌 {account_no[-4:]} 의 소유자·인증키를 찾을 수 없음 → 중단")
        return None
    user_id, simulation_yn = owner
    if simulation_yn != 'Y':
        # 실계좌 보호 — 설정에 잘못 넣어도 주문이 나가지 않는다
        logger.error(f"[MOMENTUM {market}] 계좌 {account_no[-4:]} 는 모의투자 계좌가 아니다(SIMULATION_YN={simulation_yn}) → 거부")
        return None

    tz = _market_config(market)[1]
    today = datetime.now(ZoneInfo(tz)).strftime("%Y%m%d")

    async def load_universe() -> Optional[UniverseSnapshot]:
        bars = await repo.load_universe_bars(universe_pairs(market), recent_since(HISTORY_DAYS))
        return build_universe_snapshot(bars, today, park_codes=(PARK_ETF[market]["code"],))

    broker = await KisBroker.open(db, redis, user_id, account_no, market)
    try:
        rebalancer = MomentumRebalancer(
            account_no=account_no, market=market, month=month, repo=repo, db=db, redis=redis, broker=broker,
            load_universe=load_universe, mrkt_code=mrkt_code, order_spacing_sec=settings.MOMENTUM_ORDER_SPACING_SEC)
        summary = await rebalancer.run()
        logger.info(f"[MOMENTUM {market}] 계좌 {account_no[-4:]} {month}: {summary.get('status')} · 주문 {summary.get('orders')} · "
                    f"보류 {summary.get('skipped')}")
        return summary
    finally:
        await broker.close()
