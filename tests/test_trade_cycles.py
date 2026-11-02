"""사이클 누적 수익률 (domain/trade_history/cycles.py)"""
import json
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.domain.trade_history.cycles import build_cycles, summarize_cycles

_T0 = datetime(2026, 3, 2, 9, 0)
_seq = iter(range(1, 10_000))


def buy(day, qty, price):
    return SimpleNamespace(TRADE_ID=next(_seq), TRADE_DATE=_T0 + timedelta(days=day), TRADE_TYPE="B",
                           TRADE_QTY=qty, TRADE_AMOUNT=qty * price, REALIZED_PNL=None, TRADE_REASONS=None)


def sell(day, qty, price, pnl, full=True):
    reasons = json.dumps(["전량 매도" if full else "부분 매도"], ensure_ascii=False)
    return SimpleNamespace(TRADE_ID=next(_seq), TRADE_DATE=_T0 + timedelta(days=day), TRADE_TYPE="S",
                           TRADE_QTY=qty, TRADE_AMOUNT=qty * price, REALIZED_PNL=pnl, TRADE_REASONS=reasons)


def test_repeated_cycles_compound_instead_of_resetting():
    trades = [
        buy(0, 10, 100_000), sell(10, 10, 120_000, Decimal(200_000)),   # +20%
        buy(20, 10, 120_000), sell(30, 10, 114_000, Decimal(-60_000)),  # -5%
    ]
    cycles = build_cycles(trades)
    assert [round(c["rate"], 4) for c in cycles] == [Decimal("0.2000"), Decimal("-0.0500")]
    s = summarize_cycles(cycles)
    assert s["closed_pnl"] == Decimal(140_000)
    assert round(s["closed_rate"], 4) == Decimal("0.1400")  # 1.2 × 0.95 − 1


def test_partial_then_full_sell_is_one_cycle():
    trades = [
        buy(0, 10, 100_000),
        sell(5, 5, 108_000, Decimal(40_000), full=False),
        sell(9, 5, 130_000, Decimal(150_000)),
    ]
    cycles = build_cycles(trades)
    assert len(cycles) == 1 and cycles[0]["closed"]
    assert cycles[0]["realized_pnl"] == Decimal(190_000)
    assert round(cycles[0]["rate"], 2) == Decimal("0.19")


def test_open_cycle_is_last_and_not_in_closed_summary():
    trades = [buy(0, 10, 100_000), sell(10, 10, 120_000, Decimal(200_000)), buy(20, 5, 120_000)]
    cycles = build_cycles(trades)
    assert [c["closed"] for c in cycles] == [True, False]
    assert cycles[1]["cost"] == Decimal(600_000)
    assert summarize_cycles(cycles)["closed_count"] == 1


def test_adopted_position_sell_has_no_rate_but_keeps_amount():
    trades = [sell(3, 10, 110_000, Decimal(90_000)), buy(10, 10, 100_000), sell(20, 10, 120_000, Decimal(200_000))]
    cycles = build_cycles(trades)
    assert cycles[0]["rate"] is None and not cycles[0]["cost_known"]
    assert cycles[0]["realized_pnl"] == Decimal(90_000)
    s = summarize_cycles(cycles)
    assert s["closed_pnl"] == Decimal(290_000)
    assert s["rated_count"] == 1 and s["closed_count"] == 2
    assert round(s["closed_rate"], 2) == Decimal("0.20")  # 계산 가능한 사이클만 곱한다


def test_missing_realized_pnl_makes_rate_unknown_not_zero():
    trades = [buy(0, 10, 100_000), sell(5, 10, 120_000, None)]
    cycles = build_cycles(trades)
    assert cycles[0]["closed"] and cycles[0]["rate"] is None and not cycles[0]["pnl_complete"]


def test_input_order_does_not_matter():
    trades = [sell(10, 10, 120_000, Decimal(200_000)), buy(0, 10, 100_000)]
    cycles = build_cycles(trades)
    assert len(cycles) == 1 and round(cycles[0]["rate"], 2) == Decimal("0.20")


def test_no_trades():
    assert build_cycles([]) == []
    s = summarize_cycles([])
    assert s["closed_count"] == 0 and s["closed_rate"] is None


# ── 서비스 응답 (소유권·단위) ──────────────────────────────────────────────
import asyncio

import pytest

from app.domain.trade_history.service import TradeHistoryService
from app.exceptions import NotFoundError


class _FakeRepo:
    def __init__(self, swing, trades):
        self._swing, self._trades = swing, trades

    async def find_swing_with_ownership(self, swing_id, user_id):
        return self._swing

    async def find_by_swing_id(self, swing_id):
        return self._trades


def _service(swing, trades):
    svc = TradeHistoryService.__new__(TradeHistoryService)
    svc.repo = _FakeRepo(swing, trades)
    return svc


def test_service_formats_percent_and_krw_ints():
    trades = [buy(0, 10, 100_000), sell(10, 10, 120_000, Decimal(200_000)), buy(20, 5, 120_000)]
    out = asyncio.run(_service(SimpleNamespace(MRKT_CODE="J"), trades).get_cycles("u", 1))
    assert out["CLOSED_RATE"] == 20.0 and out["CLOSED_PNL"] == 200000
    assert isinstance(out["cycles"][0]["COST"], int)
    open_cycle = out["cycles"][1]
    assert open_cycle["CLOSED"] is False and open_cycle["RATE"] is None and open_cycle["COST"] == 600000


def test_service_us_amounts_are_floats_and_unknown_rate_is_none():
    trades = [sell(1, 3, 100, Decimal("12.5"))]
    out = asyncio.run(_service(SimpleNamespace(MRKT_CODE="NAS"), trades).get_cycles("u", 1))
    assert out["CLOSED_RATE"] is None and out["CLOSED_PNL"] == 12.5
    assert out["cycles"][0]["COST_KNOWN"] is False


def test_service_rejects_foreign_swing():
    with pytest.raises(NotFoundError):
        asyncio.run(_service(None, []).get_cycles("other", 1))


def test_adopted_partial_then_full_sell_is_one_cycle():
    """편입 10주를 5주·5주로 나눠 팔아도 한 사이클 — 수량을 모르는 사이클은 '전량 매도'에서만 닫는다"""
    trades = [sell(1, 5, 110_000, Decimal(50_000), full=False), sell(9, 5, 120_000, Decimal(90_000))]
    cycles = build_cycles(trades)
    assert len(cycles) == 1 and cycles[0]["closed"]
    assert cycles[0]["realized_pnl"] == Decimal(140_000) and cycles[0]["rate"] is None
    s = summarize_cycles(cycles)
    assert s["closed_count"] == 1 and s["closed_pnl"] == Decimal(140_000)


def test_adopted_partial_sell_while_still_holding_stays_open():
    """남은 보유가 있는데 닫힌 사이클로 보이면 프론트가 평가손익을 누적에서 빼먹는다"""
    cycles = build_cycles([sell(1, 5, 110_000, Decimal(50_000), full=False)], holding=True)
    assert len(cycles) == 1 and not cycles[0]["closed"]
    assert cycles[0]["realized_pnl"] == Decimal(50_000)


def test_unrecorded_full_sell_is_closed_when_nothing_is_held_now():
    """사유 없이 기록된 편입분 매도(예: 체결 복구)도 지금 보유가 0이면 끝난 사이클"""
    t = sell(1, 10, 110_000, Decimal(90_000), full=False)
    t.TRADE_REASONS = json.dumps(["체결 복구"], ensure_ascii=False)
    assert not build_cycles([t], holding=True)[0]["closed"]
    assert build_cycles([t], holding=None)[0]["closed"] is False, "보유 여부를 모르면 닫지 않는다"
    closed = build_cycles([t], holding=False)
    assert closed[0]["closed"] and closed[0]["end"] == t.TRADE_DATE


def test_buy_only_cycle_is_never_force_closed():
    """매수만 있고 매도가 없는 사이클을 닫으면 수익률 0%로 보인다 (롤백·미확인 매수 등)"""
    cycles = build_cycles([buy(0, 10, 100_000)], holding=False)
    assert len(cycles) == 1 and not cycles[0]["closed"] and cycles[0]["rate"] is None


def test_known_cycle_unaffected_by_holding_flag():
    trades = [buy(0, 10, 100_000), sell(10, 10, 120_000, Decimal(200_000))]
    for holding in (None, True, False):
        cycles = build_cycles(trades, holding=holding)
        assert len(cycles) == 1 and round(cycles[0]["rate"], 2) == Decimal("0.20")


def test_service_passes_current_holding_to_cycles():
    trades = [sell(1, 10, 110_000, Decimal(90_000), full=False)]
    trades[0].TRADE_REASONS = json.dumps(["체결 복구"], ensure_ascii=False)
    flat = asyncio.run(_service(SimpleNamespace(MRKT_CODE="J", HOLD_QTY=0), trades).get_cycles("u", 1))
    held = asyncio.run(_service(SimpleNamespace(MRKT_CODE="J", HOLD_QTY=5), trades).get_cycles("u", 1))
    assert flat["cycles"][0]["CLOSED"] is True
    assert held["cycles"][0]["CLOSED"] is False
