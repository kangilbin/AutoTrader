"""
파킹 ETF — 놀고 있는 현금을 단기 금리형 ETF로 옮겨 두고, 매수 신호가 나면 필요한 만큼만 팔아 현금을 만든다

- 파킹(매수)은 사용자가 버튼으로 시작한다 (park_idle_cash). 자동 재파킹은 하지 않는다.
- 해제(매도)는 풀 매수 직전에 자동이다 (ensure_cash). 모자란 금액만큼만 판다 — 나머지는 계속 이자를 받는다.
- 파킹 ETF는 스윙이 아니다. 청산선·실보유 대조·열린 리스크 계산에 들어가지 않고(스윙 DB 기준으로 계산),
  총자산(equity)에는 이미 평가금이 들어 있다. 풀 매수의 '매수 가능 금액'만 현금 + 파킹 평가금으로 본다.
- 외부 호출 실패·체결 미확인은 예외 대신 결과 객체로 돌려준다. 호출부(배치)가 이번 사이클 매수만 건너뛰면 되고,
  다음 사이클에 KIS 잔고를 다시 읽으므로 실제 상태는 거기서 맞춰진다.

가정 (모의투자 실측 필요 — docs/01-plan/features/portfolio-risk-sizing.checklist.md 4-8):
  파킹 ETF를 판 직후 매수 가능 금액이 늘어 곧바로 매수할 수 있다. 해제 뒤 현금을 다시 읽어 확인하고,
  모자라면 실패로 돌려 그 매수를 건너뛴다 (다음 사이클 재시도).
"""
import logging
import math
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from app.domain.pool.entity import PARK_ETF, POOL_KR, POOL_US, PoolSetting
from app.domain.swing.trading.order_executor import SwingOrderExecutor
from app.domain.swing.trading.pool_allocator import CASH_BUFFER
from app.domain.swing.trading.pool_state import AccountSnapshot, fetch_account_snapshot
from app.external import foreign_api, kis_api

logger = logging.getLogger(__name__)

RELEASE_BUFFER = 0.01       # 해제 시 매도대금에서 깎는 여유 (시장가 하락·수수료·세금)
LOCK_TTL = 300              # 파킹 버튼·배치 풀 매수 상호 배제 락 유지 시간(초) — 배치는 후보마다 연장한다
PENDING_TTL = 120           # 체결 미확인 파킹 주문 뒤 파킹 버튼을 막는 시간(초)

PARKED = "parked"
NOTHING = "nothing"         # 파킹할 현금이 없다
UNCONFIRMED = "unconfirmed" # 주문은 나갔으나 체결 미확인 — 다음 조회에서 드러난다
FAILED = "failed"
BUSY = "busy"               # 배치 또는 다른 요청이 같은 풀을 처리 중
CLOSED = "closed"           # 정규장 시간이 아니다 — 주문을 보내지 않는다


# ==================== 순수 계산 ====================

def plan_park_qty(equity: float, cash: float, reserve_pct: float, price: float) -> int:
    """파킹 매수 수량 — 현금에서 '남길 현금(총자산 × 비율)'을 뺀 만큼, 주문 여유(CASH_BUFFER)를 얹어 계산"""
    if price <= 0 or equity <= 0:
        return 0
    spendable = cash - equity * reserve_pct
    if spendable <= 0:
        return 0
    return int(spendable // (price * (1 + CASH_BUFFER)))


def buy_cash_needed(qty: int, price: float) -> float:
    """수량 × 가격에 주문 여유를 얹은, 매수에 필요한 현금"""
    return qty * price * (1 + CASH_BUFFER)


def plan_release_qty(shortfall: float, held_qty: int, price: float) -> int:
    """모자란 금액을 메우는 데 필요한 파킹 ETF 매도 수량 — 보유 전량으로도 못 메우면 0 (팔고도 못 사는 일을 막는다)"""
    if shortfall <= 0 or held_qty <= 0 or price <= 0:
        return 0
    qty = math.ceil(shortfall / (price * (1 - RELEASE_BUFFER)))
    return qty if qty <= held_qty else 0


# ==================== 결과 ====================

@dataclass(frozen=True)
class ParkResult:
    status: str
    qty: int = 0
    amount: float = 0.0
    message: str = ""


# ensure_cash 결과 종류 — 호출부(배치)가 매수 결과로 바꾸는 기준
ENSURE_OK = "ok"                    # 필요한 현금 확보 (해제 없이 충분했던 경우 포함)
ENSURE_INSUFFICIENT = "insufficient"  # 현금과 파킹 평가금으로도 모자람 — 이 후보만 건너뛴다 (실패 아님, 더 작은 후보는 살 수 있다)
ENSURE_ERROR = "error"              # 조회·주문 실패 — 외부 장애일 수 있어 실패로 센다
ENSURE_HALT = "halt"                # 해제 결과를 모른다/해제 후에도 모자람 — 이 풀의 이번 사이클 매수를 멈춘다


@dataclass(frozen=True)
class EnsureCashResult:
    kind: str
    sold_qty: int = 0
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == ENSURE_OK


# ==================== 락 ====================

def _lock_key(account_no: str, market: str) -> str:
    return f"pool_lock:{account_no}:{market}"


async def acquire_pool_lock(redis_client, account_no: str, market: str) -> Optional[str]:
    """풀(계좌 × 시장) 주문 락 — 파킹 버튼과 배치 풀 매수가 같은 돈을 동시에 쓰지 않도록. 못 얻으면 None"""
    token = uuid.uuid4().hex
    got = await redis_client.set(_lock_key(account_no, market), token, nx=True, ex=LOCK_TTL)
    return token if got else None


async def refresh_pool_lock(redis_client, account_no: str, market: str, token: str) -> None:
    """내 락의 유지 시간을 연장한다 — 후보가 많아 긴 사이클에서 락이 중간에 풀려 파킹 버튼이 끼어드는 것을 막는다"""
    key = _lock_key(account_no, market)
    current = await redis_client.get(key)
    if isinstance(current, bytes):
        current = current.decode()
    if current == token:
        await redis_client.set(key, token, ex=LOCK_TTL)


def _pending_key(account_no: str, market: str) -> str:
    return f"pool_park_pending:{account_no}:{market}"


async def release_pool_lock(redis_client, account_no: str, market: str, token: str) -> None:
    """내가 건 락만 푼다 (TTL 만료 후 다른 쪽이 잡은 락을 지우지 않도록)"""
    key = _lock_key(account_no, market)
    current = await redis_client.get(key)
    if isinstance(current, bytes):
        current = current.decode()
    if current == token:
        await redis_client.delete(key)


# ==================== 조회 ====================

def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


async def fetch_park_holding(user_id: str, db, account_no: str, setting: PoolSetting) -> Optional[tuple]:
    """파킹 ETF 보유 (수량, 현재가) — 미보유면 (0, 0.0), 조회 실패면 None"""
    try:
        if setting.MARKET == POOL_KR:
            balance = await kis_api.get_stock_balance(user_id, db, account_no=account_no)
        else:
            balance = await foreign_api.get_us_holdings(user_id, db, account_no=account_no)
    except Exception as e:  # 외부 장애는 호출부가 이번 사이클을 건너뛰게 None 으로 알린다
        logger.error(f"[PARK] 보유 조회 실패 ({setting.park_code}): {e}")
        return None
    for item in (balance or {}).get("output1", []):
        if item.get("pdno") == setting.park_code:
            return int(_num(item.get("hldg_qty"))), _num(item.get("prpr"))
    return 0, 0.0


async def fetch_park_price(user_id: str, db, account_no: str, setting: PoolSetting) -> Optional[float]:
    """파킹 ETF 현재가 — 실패하면 None"""
    try:
        if setting.MARKET == POOL_KR:
            response = await kis_api.get_inquire_price(user_id, setting.park_code, db, account_no=account_no)
            data = response.get("output", {}) if isinstance(response, dict) else response
            price = _num((data or {}).get("stck_prpr"))
        else:
            data = await foreign_api.get_inquire_price(
                user_id, setting.park_code, db, excd=PARK_ETF[POOL_US]['excg'], account_no=account_no)
            price = _num((data or {}).get("last"))
    except Exception as e:
        logger.error(f"[PARK] 현재가 조회 실패 ({setting.park_code}): {e}")
        return None
    return price if price > 0 else None


async def fetch_park_value(user_id: str, db, account_no: str, setting: PoolSetting) -> float:
    """파킹 ETF 평가금 — 조회 실패·미보유는 0 (풀 매수 가능 금액을 늘리지 않는 보수적 방향)"""
    if not setting.parking_enabled():
        return 0.0
    holding = await fetch_park_holding(user_id, db, account_no, setting)
    return holding[0] * holding[1] if holding else 0.0


# ==================== 파킹 (버튼) ====================

async def park_idle_cash(user_id: str, db, redis_client, setting: PoolSetting) -> ParkResult:
    """유휴 현금(남길 현금 제외)을 파킹 ETF로 매수한다"""
    if not setting.parking_enabled():
        return ParkResult(FAILED, message="풀이 꺼져 있습니다")
    account_no, market = setting.ACCOUNT_NO, setting.MARKET

    # 장이 닫혀 있으면 주문을 보내지 않는다. 국내는 증권사가 거절하고, 미국은 호가를 못 읽어 실패하거나
    # (프리마켓이면) 개장까지 대기하는 지정가가 걸리는 등 시간대마다 결과가 달라 명시적으로 막는다.
    # (자동 해제는 배치가 정규장 가드를 통과한 시간에만 실행되므로 별도 확인이 필요 없다)
    from app.domain.swing.trading.auto_swing_batch import is_market_open   # 배치가 이 모듈을 import 하므로 순환 회피
    if not is_market_open(setting.park_mrkt_code):
        label = "국내 09:00~15:30" if market == POOL_KR else "미국 정규장(현지 09:30~16:00)"
        return ParkResult(CLOSED, message=f"지금은 장 운영 시간이 아닙니다 ({label}, 평일)")

    token = await acquire_pool_lock(redis_client, account_no, market)
    if token is None:
        return ParkResult(BUSY, message="자동매매가 같은 풀을 처리 중입니다. 잠시 후 다시 시도해주세요")
    try:
        if await redis_client.get(_pending_key(account_no, market)):
            return ParkResult(BUSY, message="직전 파킹 주문의 체결 확인이 끝나지 않았습니다. 잠시 후 잔고를 확인하고 다시 시도해주세요")
        snapshot = await fetch_account_snapshot(user_id, db, account_no, market, strict=True)
        if snapshot is None:
            return ParkResult(FAILED, message="계좌 잔고·주문가능금액을 읽을 수 없습니다")
        price = await fetch_park_price(user_id, db, account_no, setting)
        if price is None:
            return ParkResult(FAILED, message="파킹 ETF 현재가를 읽을 수 없습니다")

        qty = plan_park_qty(snapshot.equity, snapshot.cash, float(setting.PARK_RESERVE_PCT), price)
        if qty <= 0:
            return ParkResult(NOTHING, message="파킹할 현금이 없습니다 (남길 현금 제외)")

        filled = await SwingOrderExecutor.execute_plain(
            "buy", user_id, setting.park_code, Decimal(str(price)), qty, db, setting.park_mrkt_code, account_no)
        if not filled.get("success"):
            return ParkResult(FAILED, message=filled.get("reason", "주문 실패"))
        if filled.get("unconfirmed"):
            logger.warning(f"[PARK] {setting.park_code} 매수 {qty}주 체결 미확인 — 다음 조회에서 확인")
            # 체결됐는지 모르는 채 버튼을 또 누르면 같은 현금으로 두 번 살 수 있다 — 잠시 막는다
            await redis_client.set(_pending_key(account_no, market), "1", ex=PENDING_TTL)
            return ParkResult(UNCONFIRMED, qty=qty, message="체결을 확인하지 못했습니다. 잔고에서 확인해주세요")
        logger.info(f"[PARK] {setting.park_code} 매수 체결 {filled['qty']}/{qty}주 @ {filled['avg_price']}")
        return ParkResult(PARKED, qty=filled["qty"], amount=float(filled["amount"]))
    except Exception as e:
        logger.error(f"[PARK] 파킹 실패: {e}", exc_info=True)
        return ParkResult(FAILED, message="파킹 중 오류가 발생했습니다")
    finally:
        await release_pool_lock(redis_client, account_no, market, token)


# ==================== 해제 (매수 직전 자동) ====================

async def ensure_cash(user_id: str, db, setting: PoolSetting, needed: float, redis_client=None) -> EnsureCashResult:
    """매수에 필요한 현금(needed)을 확보한다 — 모자라면 파킹 ETF를 모자란 만큼만 판다

    호출부(풀 매수)가 이미 풀 락을 잡고 있다. 현금은 잔고를 매번 다시 읽어 판단한다.
    결과 종류(kind)로 호출부가 '이 후보만 건너뜀 / 실패 / 사이클 중단'을 가른다:
    - 해제 매도가 미확인이면 HALT — 다음 후보가 잔고를 다시 읽어도 아직 반영 전일 수 있어 같은 보유를 또 팔 수 있다.
    - 해제 뒤에도 현금이 모자라면 HALT — 정산 대기로 가정이 틀린 것이라 후보마다 ETF를 더 팔면 안 된다.
    redis_client 가 있으면 미확인 때 파킹 버튼도 잠시 막는다.
    """
    account_no, market = setting.ACCOUNT_NO, setting.MARKET

    async def _cash() -> Optional[float]:
        # 매수가능조회(주문가능 금액) 기준 — 조회가 실패하면 예수금으로 대신하지 않는다 (strict).
        # 해제 직후 재매수 가능 여부도 이 값에 반영된다: 안 늘었으면 아래에서 사이클을 멈춘다.
        snap: Optional[AccountSnapshot] = await fetch_account_snapshot(user_id, db, account_no, market, strict=True)
        return None if snap is None else snap.cash

    sell_sent = False     # 해제 매도 주문이 나갔을 수 있는 시점부터 True — 이후의 알 수 없음은 ERROR 가 아니라 HALT
    try:
        cash = await _cash()
        if cash is None:
            return EnsureCashResult(ENSURE_ERROR, message="계좌 잔고를 읽을 수 없습니다")
        if cash >= needed:
            return EnsureCashResult(ENSURE_OK)

        holding = await fetch_park_holding(user_id, db, account_no, setting)
        if holding is None:
            return EnsureCashResult(ENSURE_ERROR, message="파킹 ETF 보유를 읽을 수 없습니다")
        held_qty, price = holding
        sell_qty = plan_release_qty(needed - cash, held_qty, price)
        if sell_qty <= 0:
            return EnsureCashResult(ENSURE_INSUFFICIENT, message="현금과 파킹 ETF 평가금이 모자랍니다")

        sell_sent = True
        filled = await SwingOrderExecutor.execute_plain(
            "sell", user_id, setting.park_code, Decimal(str(price)), sell_qty, db, setting.park_mrkt_code, account_no)
        if filled.get("success") and filled.get("unconfirmed"):
            logger.warning(f"[PARK] {setting.park_code} 해제 매도 {sell_qty}주 체결 미확인 → 이번 사이클 풀 매수 중단")
            if redis_client is not None:
                await redis_client.set(_pending_key(account_no, market), "1", ex=PENDING_TTL)
            return EnsureCashResult(ENSURE_HALT, message="파킹 ETF 매도 체결을 확인하지 못했습니다")
        if not filled.get("success"):
            reason = filled.get("reason", "주문 실패")
            logger.warning(f"[PARK] {setting.park_code} 해제 매도 {sell_qty}주 실패({reason}) → 이번 매수 보류")
            return EnsureCashResult(ENSURE_ERROR, message=f"파킹 ETF 매도 실패: {reason}")
        sold = int(filled["qty"])

        cash = await _cash()   # 매도 직후 매수 가능 금액이 실제로 늘었는지 확인 (가정 검증)
        if cash is None or cash < needed:
            logger.warning(f"[PARK] 해제 {sold}주 후에도 현금 부족 (cash={cash}, needed={needed:,.0f}) → 이번 사이클 풀 매수 중단")
            return EnsureCashResult(ENSURE_HALT, sold_qty=sold, message="매도 후에도 매수 가능 금액이 모자랍니다 (정산 대기 가능)")
        logger.info(f"[PARK] {setting.park_code} {sold}주 해제 → 현금 {cash:,.0f}")
        return EnsureCashResult(ENSURE_OK, sold_qty=sold)
    except Exception as e:
        logger.error(f"[PARK] 해제 중 오류: {e}", exc_info=True)
        if sell_sent:
            # 매도 주문이 나갔을 수 있는데 체결 확인·잔고 조회가 실패 — 다음 후보가 같은 ETF 를 또 팔지 않도록 멈춘다
            if redis_client is not None:
                try:
                    await redis_client.set(_pending_key(account_no, market), "1", ex=PENDING_TTL)
                except Exception:
                    pass
            return EnsureCashResult(ENSURE_HALT, message="파킹 ETF 매도 결과를 확인하지 못했습니다")
        return EnsureCashResult(ENSURE_ERROR, message="파킹 해제 중 오류가 발생했습니다")
