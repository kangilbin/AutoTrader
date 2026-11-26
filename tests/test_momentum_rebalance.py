"""상대 모멘텀 월간 리밸런서 — 가짜 브로커·저장소·Redis 로 흐름 전체를 검증한다"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.domain.momentum import ranking as rk
from app.domain.momentum import rebalance_batch as rb
from app.domain.momentum.rebalance_batch import (
    FAILED, FILLED, PARTIAL, UNCONFIRMED, MomentumRebalancer, OrderResult, PendingResolution, UniverseSnapshot,
)
from app.domain.swing.entity import SwingTrade


# ==================== 가짜 의존성 ====================

class FakeRedis:
    def __init__(self):
        self.kv = {}

    async def get(self, k):
        return self.kv.get(k)

    async def setex(self, k, ttl, v):
        self.kv[k] = v

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    async def delete(self, k):
        self.kv.pop(k, None)


class FakeDb:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class FakeRepo:
    def __init__(self):
        self.rows = []
        self._id = 0

    async def find_momentum_swings(self, account_no, market):
        return [s for s in self.rows if s.ACCOUNT_NO == account_no and s.SWING_TYPE == "M"]

    async def find_swing(self, account_no, mrkt_code, st_code):
        return next((s for s in self.rows if (s.ACCOUNT_NO, s.MRKT_CODE, s.ST_CODE) == (account_no, mrkt_code, st_code)), None)

    async def save(self, swing):
        if swing.SWING_ID is None:
            self._id += 1
            swing.SWING_ID = self._id
            self.rows.append(swing)
        return swing

    def add_other_type(self, account_no, mrkt, code, swing_type="S"):
        s = SwingTrade.create(account_no, mrkt, code, swing_type)
        s.USE_YN = "Y"
        self._id += 1
        s.SWING_ID = self._id
        self.rows.append(s)
        return s


class FakeBroker:
    def __init__(self, equity=10_000_000.0, cash=10_000_000.0, price=100.0):
        self.equity, self.cash, self.default_price = equity, cash, price
        self.prices = {}
        self.kis = {}                       # 종목 → 보유 수량 (KIS 잔고)
        self.orders = []
        self.fail_buy, self.unconfirmed_buy, self.unconfirmed_sell = set(), set(), set()
        self.partial_sell = {}              # 종목 → 이번에 체결될 수량
        self.ensure = rb.CASH_OK
        self.holdings_ok = True
        self.pending_result = None

    async def price(self, mrkt, code):
        return self.prices.get(code, self.default_price)

    async def holdings(self):
        return dict(self.kis) if self.holdings_ok else None

    async def snapshot(self):
        return self.equity, self.cash

    async def ensure_cash(self, needed):
        return self.ensure

    async def buy(self, swing, price, qty, reasons):
        self.orders.append(("buy", swing.ST_CODE, qty, reasons))
        if swing.ST_CODE in self.fail_buy:
            return OrderResult(FAILED, reason="주문 거절")
        if swing.ST_CODE in self.unconfirmed_buy:
            return OrderResult(UNCONFIRMED, pending={"type": "buy", "ord_qty": qty})
        self.kis[swing.ST_CODE] = self.kis.get(swing.ST_CODE, 0) + qty
        self.cash -= qty * price
        return OrderResult(FILLED, qty=qty, avg_price=price, amount=qty * price)

    async def sell(self, swing, price, qty, reasons):
        self.orders.append(("sell", swing.ST_CODE, qty, reasons))
        if swing.ST_CODE in self.unconfirmed_sell:
            return OrderResult(UNCONFIRMED, pending={"type": "sell", "ord_qty": qty})
        filled = min(self.partial_sell.get(swing.ST_CODE, qty), qty)
        self.kis[swing.ST_CODE] = self.kis.get(swing.ST_CODE, 0) - filled
        self.cash += filled * price
        return OrderResult(FILLED if filled >= qty else PARTIAL, qty=filled, avg_price=price, amount=filled * price)

    async def resolve_pending(self, swing, price, pending):
        return self.pending_result or PendingResolution(completed=False, pending_state=pending)


ACCOUNT, MARKET, MRKT = "5020627801", "US", "NAS"


def _snapshot(top_codes, n=30, bars=200):
    """top_codes 가 점수 상위가 되도록 만든 유니버스 — 코드 S00..S{n-1}, top_codes 순서대로 점수가 높다"""
    closes = {}
    order = list(top_codes) + [f"S{i:02d}" for i in range(n) if f"S{i:02d}" not in top_codes]
    for rank, code in enumerate(order):
        growth = 1.0 + (n - rank) * 0.05                 # 순위가 높을수록 성장률이 크다
        closes[code] = list(np.linspace(100.0, 100.0 * growth, bars))
    return UniverseSnapshot(closes=closes, adv={c: 1e12 for c in closes}, mrkt={c: MRKT for c in closes}, latest="20261030")


TOP_A = [f"S{i:02d}" for i in range(29, 19, -1)]          # S29 .. S20


def _make(repo, redis, broker, snap, month="202611", now=None, db=None):
    async def load():
        return snap
    async def nosleep(_):
        return None
    return MomentumRebalancer(account_no=ACCOUNT, market=MARKET, month=month, repo=repo, db=db or FakeDb(), redis=redis,
                              broker=broker, load_universe=load, mrkt_code=MRKT, now=now, sleep=nosleep)


# ==================== 시나리오 ====================

@pytest.mark.asyncio
async def test_first_month_buys_top10_in_rank_order_and_finishes():
    repo, redis, broker = FakeRepo(), FakeRedis(), FakeBroker()
    reb = _make(repo, redis, broker, _snapshot(TOP_A))
    summary = await reb.run()
    assert summary["status"] == "done"
    assert [o[1] for o in broker.orders] == TOP_A                       # 순위순
    assert all(o[0] == "buy" and o[2] == 9500 for o in broker.orders)   # 총자산 9.5% ÷ 100
    held = [s for s in repo.rows if s.HOLD_QTY]
    assert len(held) == 10 and all(s.SWING_TYPE == "M" and s.USE_YN == "Y" and s.SIGNAL == 1 for s in held)
    assert broker.orders[0][3][0] == "모멘텀 편입" and "1위" in broker.orders[0][3]


@pytest.mark.asyncio
async def test_second_run_same_month_does_nothing():
    repo, redis, broker = FakeRepo(), FakeRedis(), FakeBroker()
    await _make(repo, redis, broker, _snapshot(TOP_A)).run()
    n = len(broker.orders)
    summary = await _make(repo, redis, broker, _snapshot(TOP_A)).run()
    assert summary["status"] == "already_done" and len(broker.orders) == n


@pytest.mark.asyncio
async def test_next_month_trades_only_dropouts_and_new_entrants():
    repo, redis, broker = FakeRepo(), FakeRedis(), FakeBroker()
    await _make(repo, redis, broker, _snapshot(TOP_A), month="202611").run()
    broker.orders.clear()
    new_top = [f"S{i:02d}" for i in range(28, 18, -1)]                   # S29 탈락, S19 진입
    summary = await _make(repo, redis, broker, _snapshot(new_top), month="202612").run()
    assert summary["status"] == "done"
    assert [(o[0], o[1]) for o in broker.orders] == [("sell", "S29"), ("buy", "S19")]
    s29 = next(s for s in repo.rows if s.ST_CODE == "S29")
    assert s29.HOLD_QTY == 0 and s29.SIGNAL == 0                         # 쿨다운 없이 미보유
    assert sum(1 for s in repo.rows if s.HOLD_QTY) == 10


@pytest.mark.asyncio
async def test_partial_fill_when_cash_is_short_then_waits_for_more_cash():
    repo, redis, broker = FakeRepo(), FakeRedis(), FakeBroker(cash=1_600_000.0)
    summary = await _make(repo, redis, broker, _snapshot(TOP_A)).run()
    qtys = [o[2] for o in broker.orders]
    assert qtys[0] == 9500 and 4750 <= qtys[1] < 9500                    # 두 번째는 현금만큼만 (목표의 50% 이상)
    assert len(qtys) == 2 and summary["status"] == "running"              # 나머지는 현금 부족 — 일시적이라 다음 실행에 재시도
    assert "현금 부족" in summary["skipped"].values()
    state = json.loads(redis.kv["momentum:state:5020627801:US:202611"])
    assert state["buy_failed"] == {}                                     # 일시적 실패는 영구 기록하지 않는다
    broker.cash = 10_000_000.0                                           # 현금이 들어오면 다음 실행에서 이어서 산다
    await _make(repo, redis, broker, _snapshot(TOP_A)).run()
    assert sum(1 for s in repo.rows if s.HOLD_QTY) == 10


@pytest.mark.asyncio
async def test_cash_below_half_of_target_is_not_bought():
    repo, redis, broker = FakeRepo(), FakeRedis(), FakeBroker(cash=400_000.0)
    summary = await _make(repo, redis, broker, _snapshot(TOP_A)).run()
    assert broker.orders == [] and summary["status"] == "running"


@pytest.mark.asyncio
async def test_backfill_replaces_unbuyable_candidates():
    repo, redis, broker = FakeRepo(), FakeRedis(), FakeBroker()
    repo.add_other_type(ACCOUNT, MRKT, "S25", "S")                       # 다른 타입으로 이미 등록된 종목 → 못 산다
    broker.prices["S27"] = 1e9                                           # 목표 금액으로 1주도 못 사는 종목
    summary = await _make(repo, redis, broker, _snapshot(TOP_A)).run()
    bought = [o[1] for o in broker.orders if o[0] == "buy"]
    assert "S25" not in bought and "S27" not in bought
    assert bought[-2:] == ["S00", "S01"]                                 # 이 유니버스의 11·12위(S19 가 아니라 S00·S01)로 대체
    assert sum(1 for s in repo.rows if s.HOLD_QTY and s.SWING_TYPE == "M") == 10
    state = json.loads(redis.kv["momentum:state:5020627801:US:202611"])
    assert set(state["buy_failed"]) == {"S25", "S27"} and summary["status"] == "done"


@pytest.mark.asyncio
async def test_failed_order_is_retried_next_run_not_marked_permanent():
    repo, redis, broker = FakeRepo(), FakeRedis(), FakeBroker()
    broker.fail_buy = {"S29"}
    summary = await _make(repo, redis, broker, _snapshot(TOP_A)).run()
    assert "S29" in summary["skipped"] and "주문 거절" in summary["skipped"]["S29"]
    state = json.loads(redis.kv["momentum:state:5020627801:US:202611"])
    assert "S29" not in state["buy_failed"]
    broker.fail_buy = set()
    await _make(repo, redis, broker, _snapshot(TOP_A)).run()
    assert next(s for s in repo.rows if s.ST_CODE == "S29").HOLD_QTY == 9500


@pytest.mark.asyncio
async def test_unconfirmed_buy_is_resolved_on_next_run():
    repo, redis, broker = FakeRepo(), FakeRedis(), FakeBroker()
    broker.unconfirmed_buy = {"S29"}
    await _make(repo, redis, broker, _snapshot(TOP_A)).run()
    s29 = next(s for s in repo.rows if s.ST_CODE == "S29")
    assert (s29.HOLD_QTY or 0) == 0 and f"pending_order:{s29.SWING_ID}" in redis.kv
    broker.unconfirmed_buy = set()
    broker.kis["S29"] = 9500                                              # 사실 체결됐다
    broker.pending_result = PendingResolution(completed=True, hold_qty=9500, entry_price=100.0)
    await _make(repo, redis, broker, _snapshot(TOP_A)).run()
    assert s29.HOLD_QTY == 9500 and s29.SIGNAL == 1
    assert f"pending_order:{s29.SWING_ID}" not in redis.kv


@pytest.mark.asyncio
async def test_partial_sell_keeps_remaining_and_blocks_buys_until_done():
    repo, redis, broker = FakeRepo(), FakeRedis(), FakeBroker()
    await _make(repo, redis, broker, _snapshot(TOP_A), month="202611").run()
    broker.orders.clear()
    new_top = [f"S{i:02d}" for i in range(28, 18, -1)]
    broker.partial_sell = {"S29": 4000}
    summary = await _make(repo, redis, broker, _snapshot(new_top), month="202612").run()
    s29 = next(s for s in repo.rows if s.ST_CODE == "S29")
    assert s29.HOLD_QTY == 5500 and summary["status"] == "running"
    assert [o[0] for o in broker.orders] == ["sell"]                      # 매도가 끝나기 전에는 매수하지 않는다
    assert summary["skipped"]["buy"] == "매도 미완료"
    broker.partial_sell = {}
    summary = await _make(repo, redis, broker, _snapshot(new_top), month="202612").run()
    assert s29.HOLD_QTY == 0 and summary["status"] == "done"
    assert [(o[0], o[1], o[2]) for o in broker.orders[1:]] == [("sell", "S29", 5500), ("buy", "S19", 9500)]


@pytest.mark.asyncio
async def test_reconcile_syncs_db_to_kis_and_ignores_unmanaged_holdings():
    repo, redis, broker = FakeRepo(), FakeRedis(), FakeBroker()
    await _make(repo, redis, broker, _snapshot(TOP_A), month="202611").run()
    broker.kis["S29"] = 0                                                 # 밖에서 팔았다
    broker.kis["S20"] = 7000                                              # 밖에서 일부 더 샀다/줄였다
    broker.kis["ZZZ999"] = 123                                            # 직접 산 종목 — 행이 없어 건드리지 않는다
    broker.orders.clear()
    broker.cash = 10_000_000.0                                            # 현금은 충분하다 (이 테스트는 대조만 본다)
    summary = await _make(repo, redis, broker, _snapshot(TOP_A), month="202612").run()
    s29 = next(s for s in repo.rows if s.ST_CODE == "S29")
    s20 = next(s for s in repo.rows if s.ST_CODE == "S20")
    assert s20.HOLD_QTY == 7000
    assert set(summary["reconciled"]) == {"S29", "S20"}
    assert all(o[1] != "ZZZ999" for o in broker.orders)
    assert s29.HOLD_QTY == 9500 and s29.SIGNAL == 1                      # 대조로 비워진 뒤 상위 10 에 있으니 다시 편입(재매수)


@pytest.mark.asyncio
async def test_no_ranking_when_universe_too_small_leaves_no_state():
    repo, redis, broker = FakeRepo(), FakeRedis(), FakeBroker()
    summary = await _make(repo, redis, broker, _snapshot(TOP_A[:5], n=12)).run()
    assert summary["status"] == "no_ranking" and broker.orders == [] and not redis.kv


@pytest.mark.asyncio
async def test_unreadable_holdings_stops_everything():
    repo, redis, broker = FakeRepo(), FakeRedis(), FakeBroker()
    broker.holdings_ok = False
    summary = await _make(repo, redis, broker, _snapshot(TOP_A)).run()
    assert summary["status"] == "no_holdings" and broker.orders == [] and not redis.kv


@pytest.mark.asyncio
async def test_old_plan_is_abandoned_after_cutoff():
    repo, redis, broker = FakeRepo(), FakeRedis(), FakeBroker(cash=400_000.0)
    t0 = datetime(2026, 11, 2)
    await _make(repo, redis, broker, _snapshot(TOP_A), now=t0).run()
    state = json.loads(redis.kv["momentum:state:5020627801:US:202611"])
    assert state["status"] == "running"
    summary = await _make(repo, redis, broker, _snapshot(TOP_A), now=t0 + timedelta(days=rb.CUTOFF_DAYS)).run()
    assert summary["status"] == "done"


@pytest.mark.asyncio
async def test_halt_from_parking_release_stops_buying_this_run():
    repo, redis, broker = FakeRepo(), FakeRedis(), FakeBroker()
    broker.ensure = rb.CASH_HALT
    summary = await _make(repo, redis, broker, _snapshot(TOP_A)).run()
    assert broker.orders == [] and summary["status"] == "running"


@pytest.mark.asyncio
async def test_liquidity_cap_reduces_quantity_instead_of_skipping():
    repo, redis, broker = FakeRepo(), FakeRedis(), FakeBroker()
    snap = _snapshot(TOP_A)
    snap.adv["S29"] = 5_000_000.0                                         # 5% = 250,000원 → 2,500주
    await _make(repo, redis, broker, snap).run()
    assert broker.orders[0][1] == "S29" and broker.orders[0][2] == 2500


# ==================== 순위 입력 / 보조 ====================

def _bars(n=200, last="20261030", stale_days=0):
    dates = pd.bdate_range(end=pd.Timestamp(last) - pd.Timedelta(days=stale_days), periods=n + 5)[-n:].strftime("%Y%m%d")
    close = np.linspace(100, 150, n)
    return pd.DataFrame({"STCK_BSOP_DATE": dates, "STCK_OPRC": close, "STCK_HGPR": close * 1.01, "STCK_LWPR": close * 0.99,
                         "STCK_CLPR": close, "ACML_VOL": np.full(n, 1_000_000.0)})


def test_build_snapshot_excludes_today_stale_and_park_and_computes_adv():
    bars = {("NAS", "AAA"): _bars(), ("NAS", "OLD"): _bars(stale_days=40), ("NYS", "SGOV"): _bars(),
            ("NAS", "TODAY"): pd.concat([_bars(), _bars().tail(1).assign(STCK_BSOP_DATE="20261101")], ignore_index=True)}
    snap = rb.build_universe_snapshot(bars, today="20261101", park_codes=("SGOV",))
    assert set(snap.closes) == {"AAA", "TODAY"}                           # 오래된 종목·파킹 ETF 제외
    assert snap.latest == "20261030" and len(snap.closes["TODAY"]) == 200  # 오늘(20261101) 봉은 제외
    assert snap.adv["AAA"] == pytest.approx(150.0 * 1_000_000.0, rel=0.02)
    assert snap.mrkt["AAA"] == "NAS"


def test_build_snapshot_empty_returns_none():
    assert rb.build_universe_snapshot({}, today="20261101") is None


def test_parse_accounts():
    assert rb._parse_accounts(" 111, 222 ,,") == ["111", "222"]
    assert rb._parse_accounts("") == []


# ==================== 엔티티·쿼리 제외 ====================

def test_swing_entity_momentum_transitions():
    s = SwingTrade.create(ACCOUNT, MRKT, "AAA", "M")
    assert s.is_momentum() and s.SIGNAL is None or s.SIGNAL in (None, 0)
    s.enter_momentum(100.5, 50)
    assert s.SIGNAL == 1 and s.HOLD_QTY == 50 and float(s.ENTRY_PRICE) == pytest.approx(100.5)
    s.reduce_momentum(20)
    assert s.HOLD_QTY == 20 and s.SIGNAL == 1
    s.reduce_momentum(0)
    assert s.HOLD_QTY == 0 and s.SIGNAL == 0 and s.ENTRY_PRICE is None
    with pytest.raises(Exception):
        s.enter_momentum(100, 0)


def test_swing_entity_rejects_unknown_type_but_accepts_momentum():
    SwingTrade.create(ACCOUNT, MRKT, "AAA", "M")
    with pytest.raises(Exception):
        SwingTrade.create(ACCOUNT, MRKT, "AAA", "Z")


def test_signal_based_queries_exclude_momentum_rows():
    """신호 기반 배치·풀 계산의 조회가 SWING_TYPE='M' 행을 건드리지 않는다 (소스 수준 회귀 방지)"""
    src = (Path(__file__).resolve().parents[1] / "app/domain/swing/repository.py").read_text(encoding="utf-8")
    assert src.count("ST.SWING_TYPE <> 'M'") == 3                          # find_active_swings / domestic / overseas
    assert "SwingTrade.SWING_TYPE != 'M'" in src.split("async def find_holdings")[1].split("async def find_by_account_nos")[0]
    assert "SwingTrade.SWING_TYPE != 'M'" in src.split("async def find_active_stock_codes")[1]


def test_universe_file_is_consistent_with_research_rules():
    from app.domain.momentum.universe import universe_pairs
    for mk in ("KR", "US"):
        pairs = universe_pairs(mk)
        assert len(pairs) >= rk.MIN_UNIVERSE * 3 and len(set(pairs)) == len(pairs)
    assert ("J", "069500") not in universe_pairs("KR") and ("AMS", "SPY") not in universe_pairs("US")


# ==================== 잡 진입점 · 실계좌 보호 ====================

@pytest.mark.asyncio
async def test_run_account_refuses_non_simulation_account(monkeypatch):
    import app.domain.momentum.kis_broker as kb
    import app.domain.momentum.repository as repo_mod

    class FakeMR:
        def __init__(self, db):
            pass

        async def find_account_owner(self, account_no):
            return ("user1", "N")                                        # 실계좌

    async def must_not_open(*a, **k):
        raise AssertionError("실계좌에서는 브로커를 열면 안 된다")

    monkeypatch.setattr(repo_mod, "MomentumRepository", FakeMR)
    monkeypatch.setattr(kb.KisBroker, "open", must_not_open)
    out = await rb._run_account(FakeDb(), FakeRedis(), "1111111111", "KR", "202611", "J", settings=None)
    assert out is None


@pytest.mark.asyncio
async def test_run_account_returns_none_when_owner_unknown(monkeypatch):
    import app.domain.momentum.repository as repo_mod

    class FakeMR:
        def __init__(self, db):
            pass

        async def find_account_owner(self, account_no):
            return None

    monkeypatch.setattr(repo_mod, "MomentumRepository", FakeMR)
    assert await rb._run_account(FakeDb(), FakeRedis(), "1111111111", "KR", "202611", "J", settings=None) is None


@pytest.mark.asyncio
async def test_run_account_on_simulation_account_runs_rebalancer(monkeypatch):
    import app.domain.momentum.kis_broker as kb
    import app.domain.momentum.repository as repo_mod

    closed = []

    class FakeMR(FakeRepo):
        def __init__(self, db):
            super().__init__()

        async def find_account_owner(self, account_no):
            return ("user1", "Y")

        async def load_universe_bars(self, pairs, since):
            return {}                                                    # 데이터 없음 → 순위 없음

    class Settings:
        MOMENTUM_ORDER_SPACING_SEC = 0.0

    async def fake_open(db, redis, user_id, account_no, market):
        b = FakeBroker()
        async def close():
            closed.append(True)
        b.close = close
        return b

    monkeypatch.setattr(repo_mod, "MomentumRepository", FakeMR)
    monkeypatch.setattr(kb.KisBroker, "open", fake_open)
    out = await rb._run_account(FakeDb(), FakeRedis(), "5020627801", "US", "202611", "NAS", Settings())
    assert out["status"] == "no_ranking" and closed == [True]            # 브로커는 항상 닫는다


@pytest.mark.asyncio
async def test_job_is_noop_when_disabled(monkeypatch):
    import app.core.config as cfg

    class S:
        MOMENTUM_ENABLED = False
        MOMENTUM_ACCOUNT_NOS = "5020627801"
        ALLOW_OFFHOURS_TRADING = True

    monkeypatch.setattr(cfg, "get_settings", lambda: S())
    import app.common.redis as redis_mod

    async def boom(*a, **k):
        raise AssertionError("꺼져 있으면 Redis 에도 접근하지 않는다")

    monkeypatch.setattr(redis_mod.Redis, "get_connection", boom)
    await rb.momentum_rebalance_job("US")
