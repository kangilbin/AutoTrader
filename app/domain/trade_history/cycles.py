"""
매매 사이클 누적 수익률 — 체결 이력을 (매수 → 전량 매도) 사이클로 묶는다.

풀 모드(INIT_AMOUNT=0)에는 종목별 원금이 없어 재매수하면 수익률이 새로 시작하는 것처럼 보인다.
사이클별 수익률을 따로 남기고 이어서 곱하면(거래 기준) 반복 매매해도 누적 성과를 볼 수 있다.

수익률 = 사이클의 매도 실현손익 합 ÷ 사이클의 매수금액.
매수 이력이 없는 사이클(기존 보유 편입)이나 실현손익이 비어 있는 매도가 낀 사이클은
모르는 값을 0으로 채우지 않고 rate=None 으로 둔다 (금액만 표시).
"""
import json
from decimal import Decimal
from typing import Any, Iterable, List, Optional


def _to_decimal(value: Any) -> Optional[Decimal]:
    return None if value is None else Decimal(str(value))


def _is_full_sell(reasons: Optional[str]) -> bool:
    """TRADE_REASONS(JSON 배열 문자열)에 '전량'이 있으면 전량 매도."""
    if not reasons:
        return False
    try:
        items = json.loads(reasons)
    except (ValueError, TypeError):
        return False
    return isinstance(items, list) and any("전량" in str(item) for item in items)


def _finish(cycle: dict, end) -> dict:
    """사이클을 닫고, 매수 이력과 실현손익을 모두 아는 경우에만 수익률을 계산한다"""
    cycle["end"] = end
    cycle["closed"] = True
    if cycle["cost_known"] and cycle["pnl_complete"] and cycle["cost"] > 0:
        cycle["rate"] = cycle["realized_pnl"] / cycle["cost"]
    return cycle


def build_cycles(trades: Iterable[Any], holding: Optional[bool] = None) -> List[dict]:
    """체결 이력 → 사이클 목록 (시간순, 마지막이 진행 중일 수 있음).

    holding: 지금 이 스윙이 보유 중인가 (모르면 None). 매수 이력이 없는 사이클(편입 포지션)은 수량을 알 수 없어
    사유의 '전량 매도'로만 닫는데, 사유가 남지 않은 전량 매도도 있을 수 있다 — 보유가 0 이면 매도가 있었던
    열린 사이클은 끝난 것으로 본다. (매수만 있고 매도가 없는 사이클은 닫지 않는다: 닫으면 수익률 0% 로 보인다)

    사이클 dict: start, end(진행 중이면 None), cost(매수금액), realized_pnl(확정 손익 합),
    rate(닫힌 사이클만, 계산 불가면 None), closed, cost_known, pnl_complete
    """
    ordered = sorted(trades, key=lambda t: (t.TRADE_DATE, t.TRADE_ID or 0))
    cycles: List[dict] = []
    cur: Optional[dict] = None
    held = 0

    for t in ordered:
        qty = int(t.TRADE_QTY)
        if cur is None:
            cur = {"start": t.TRADE_DATE, "end": None, "cost": Decimal(0), "realized_pnl": Decimal(0),
                   "rate": None, "closed": False, "cost_known": True, "pnl_complete": True, "sells": 0}
            held = 0
        last_date = t.TRADE_DATE

        if t.TRADE_TYPE == "B":
            cur["cost"] += Decimal(str(t.TRADE_AMOUNT))
            held += qty
            continue

        # 매도
        held -= qty
        cur["sells"] += 1
        if held < 0 or cur["cost"] == 0:
            cur["cost_known"] = False  # 이력에 없는 매수분(편입)을 판 것
        pnl = _to_decimal(t.REALIZED_PNL)
        if pnl is None:
            cur["pnl_complete"] = False
        else:
            cur["realized_pnl"] += pnl

        # 수량으로 닫는 것은 매수 이력을 아는 사이클뿐이다 — 편입분의 부분 매도(held < 0)로 닫으면
        # 한 사이클이 둘로 쪼개지고, 남은 보유가 있는데도 닫힌 것으로 보인다
        if _is_full_sell(t.TRADE_REASONS) or (cur["cost_known"] and held <= 0):
            cycles.append(_finish(cur, t.TRADE_DATE))
            cur = None

    if cur is not None:
        if holding is False and cur["sells"] > 0:
            cycles.append(_finish(cur, last_date))
        else:
            cycles.append(cur)  # 진행 중 사이클
    return cycles


def summarize_cycles(cycles: List[dict]) -> dict:
    """닫힌 사이클 누적. 수익률은 계산 가능한 사이클만 곱하고(거래 기준), 금액은 전부 더한다."""
    closed = [c for c in cycles if c["closed"]]
    rated = [c for c in closed if c["rate"] is not None]
    compounded = Decimal(1)
    for c in rated:
        compounded *= (1 + c["rate"])
    return {
        "closed_count": len(closed),
        "closed_pnl": sum((c["realized_pnl"] for c in closed), Decimal(0)),
        "closed_rate": (compounded - 1) if rated else None,
        "rated_count": len(rated),  # closed_count 보다 작으면 일부 사이클은 수익률에서 빠진 것
    }
