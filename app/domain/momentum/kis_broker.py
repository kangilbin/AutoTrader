"""상대 모멘텀의 실제 브로커 — KIS 시세·잔고·주문 + 파킹 ETF 해제를 기존 컴포넌트로 연결한다

rebalance_batch.Broker 인터페이스의 실전 구현이다. 새로 만든 로직은 없고 기존 경로를 그대로 부른다:
- 주문·체결 확인·TRADE_HISTORY 저장: SwingOrderExecutor.execute_buy / execute_sell (저장 지점 단일화 유지)
- 총자산·매수 가능 현금: pool_state.fetch_account_snapshot
- 파킹 ETF 해제: parking.ensure_cash (+ 풀 락)
- 체결 미확인 재확인: SwingOrderExecutor.resolve_pending_order
"""
import logging
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from app.domain.momentum.rebalance_batch import (
    CASH_FAILED, CASH_HALT, CASH_INSUFFICIENT, CASH_OK, FAILED, FILLED, PARTIAL, UNCONFIRMED,
    OrderResult, PendingResolution,
)
from app.domain.pool.entity import PoolSetting
from app.domain.pool.repository import PoolRepository
from app.domain.swing.entity import SwingTrade
from app.domain.swing.trading import parking
from app.domain.swing.trading.order_executor import SwingOrderExecutor
from app.domain.swing.trading.pool_state import fetch_account_snapshot
from app.external import foreign_api, kis_api

logger = logging.getLogger(__name__)


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


class KisBroker:
    """한 계좌·시장의 KIS 접근. KisBroker.open(...) 으로 만들고 끝나면 close() 로 파킹 락을 놓는다"""

    def __init__(self, db, redis, user_id: str, account_no: str, market: str, setting: Optional[PoolSetting]):
        self.db, self.redis = db, redis
        self.user_id, self.account_no, self.market = user_id, account_no, market
        self.setting = setting
        self.equity: Optional[float] = None
        self._lock_token: Optional[str] = None

    @classmethod
    async def open(cls, db, redis, user_id: str, account_no: str, market: str) -> "KisBroker":
        setting = await PoolRepository(db).find(account_no, market)
        broker = cls(db, redis, user_id, account_no, market, setting)
        if broker._parking:
            # 파킹 버튼·다른 배치와 같은 풀을 동시에 건드리지 않는다
            broker._lock_token = await parking.acquire_pool_lock(redis, account_no, market)
            if broker._lock_token is None:
                logger.warning(f"[MOMENTUM {market}] 풀 {account_no[-4:]} 파킹 처리 중 → 파킹 없이 진행")
                broker.setting = None
        return broker

    async def close(self) -> None:
        if self._lock_token is not None:
            try:
                await parking.release_pool_lock(self.redis, self.account_no, self.market, self._lock_token)
            except Exception as e:     # 락은 TTL 로 풀린다
                logger.error(f"[MOMENTUM {self.market}] 풀 락 해제 실패(TTL 로 해제됨): {e}")

    @property
    def _parking(self) -> bool:
        return self.setting is not None and self.setting.parking_enabled()

    # ---------- 시세·잔고 ----------

    async def price(self, mrkt_code: str, code: str) -> Optional[float]:
        try:
            if self.market == "KR":
                response = await kis_api.get_inquire_price(self.user_id, code, self.db, account_no=self.account_no)
                data = response.get("output", {}) if isinstance(response, dict) else response
                price = _num((data or {}).get("stck_prpr"))
            else:
                data = await foreign_api.get_inquire_price(
                    self.user_id, code, self.db, excd=mrkt_code, account_no=self.account_no)
                price = _num((data or {}).get("last"))
        except Exception as e:
            logger.error(f"[MOMENTUM {self.market}] {code} 현재가 조회 실패: {e}")
            return None
        return price if price > 0 else None

    async def holdings(self) -> Optional[Dict[str, int]]:
        try:
            if self.market == "KR":
                balance = await kis_api.get_stock_balance(self.user_id, self.db, account_no=self.account_no)
            else:
                balance = await foreign_api.get_us_holdings(self.user_id, self.db, account_no=self.account_no)
        except Exception as e:
            logger.error(f"[MOMENTUM {self.market}] 잔고 조회 실패: {e}")
            return None
        return {item["pdno"]: int(_num(item.get("hldg_qty")))
                for item in (balance or {}).get("output1", []) if item.get("pdno")}

    async def snapshot(self) -> Optional[Tuple[float, float]]:
        snap = await fetch_account_snapshot(self.user_id, self.db, self.account_no, self.market)
        if snap is None:
            return None
        self.equity = snap.equity
        cash = snap.cash
        if self._parking:
            # 파킹 ETF 평가금은 총자산에 이미 들어 있고, 모자란 현금은 매수 직전에 파킹을 팔아 만든다
            cash = min(cash + await parking.fetch_park_value(self.user_id, self.db, self.account_no, self.setting),
                       snap.equity)
        return snap.equity, cash

    async def ensure_cash(self, needed: float) -> str:
        if not self._parking:
            return CASH_OK
        await parking.refresh_pool_lock(self.redis, self.account_no, self.market, self._lock_token)
        result = await parking.ensure_cash(self.user_id, self.db, self.setting, needed, self.redis)
        if result.ok:
            return CASH_OK
        logger.warning(f"[MOMENTUM {self.market}] 파킹 해제로 현금을 만들지 못함: {result.message}")
        return {parking.ENSURE_INSUFFICIENT: CASH_INSUFFICIENT, parking.ENSURE_HALT: CASH_HALT}.get(result.kind, CASH_FAILED)

    # ---------- 주문 ----------

    async def buy(self, swing: SwingTrade, price: float, qty: int, reasons: List[str]) -> OrderResult:
        try:
            res = await SwingOrderExecutor.execute_buy(
                swing_id=swing.SWING_ID, user_id=self.user_id, st_code=swing.ST_CODE, current_price=Decimal(str(price)),
                qty=qty, signal_on_complete=1, db=self.db, mrkt_code=swing.MRKT_CODE, reasons=list(reasons),
                account_no=self.account_no, equity=self.equity)
        except Exception as e:
            # 주문이 나갔을 수 있는데 이후 단계에서 실패 — 체결됐다면 다음 실행의 실보유 대조가 반영한다
            logger.error(f"[MOMENTUM {self.market}] {swing.ST_CODE} 매수 처리 중 오류: {e}", exc_info=True)
            return OrderResult(FAILED, reason=f"예외: {e}")
        if not res.get("success"):
            return OrderResult(FAILED, reason=res.get("reason", "주문 실패"))
        if res.get("unconfirmed"):
            return OrderResult(UNCONFIRMED, pending=res.get("pending_order"))
        return OrderResult(FILLED, qty=int(res.get("qty", 0)), avg_price=float(res.get("avg_price", price)),
                           amount=float(res.get("amount", 0)))

    async def sell(self, swing: SwingTrade, price: float, qty: int, reasons: List[str]) -> OrderResult:
        try:
            res = await SwingOrderExecutor.execute_sell(
                swing_id=swing.SWING_ID, user_id=self.user_id, st_code=swing.ST_CODE, current_price=Decimal(str(price)),
                target_qty=qty, signal_on_complete=0, db=self.db, mrkt_code=swing.MRKT_CODE, reasons=list(reasons),
                account_no=self.account_no)
        except Exception as e:
            logger.error(f"[MOMENTUM {self.market}] {swing.ST_CODE} 매도 처리 중 오류: {e}", exc_info=True)
            return OrderResult(FAILED, reason=f"예외: {e}")
        if not res.get("success"):
            return OrderResult(FAILED, reason=res.get("reason", "주문 실패"))
        if res.get("unconfirmed"):
            return OrderResult(UNCONFIRMED, pending=res.get("pending_order"))
        filled = int(res.get("qty", 0))
        return OrderResult(FILLED if filled >= qty else PARTIAL, qty=filled, avg_price=float(res.get("avg_price", price)),
                           amount=float(res.get("amount", 0)))

    async def resolve_pending(self, swing: SwingTrade, price: float, pending: dict) -> PendingResolution:
        res = await SwingOrderExecutor.resolve_pending_order(
            pending=pending, swing_id=swing.SWING_ID, user_id=self.user_id, st_code=swing.ST_CODE,
            current_price=Decimal(str(price)), current_entry_price=float(swing.ENTRY_PRICE or 0),
            current_hold_qty=int(swing.HOLD_QTY or 0), db=self.db, account_no=self.account_no)
        return PendingResolution(
            completed=bool(res.get("completed")), hold_qty=res.get("hold_qty") if res.get("completed") else None,
            entry_price=res.get("entry_price") if res.get("completed") else None,
            pending_state=res.get("pending_state"))
