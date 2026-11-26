"""일목균형표 실전 전략(SWING_TYPE='I') — 신호, 오케스트레이터 훅, 풀 사이징, 지표 캐시, 설명, EMA 기본 동작 보존"""
import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd

from app.domain.pool.entity import PoolSetting
from app.domain.swing import risk_sizing
from app.domain.swing.entity import SwingTrade
from app.domain.swing.trading import auto_swing_batch as batch
from app.domain.swing.trading import pool_allocator as pa
from app.domain.swing.trading import pool_state as ps
from app.domain.swing.trading.pool_allocator import EntryCandidate, run_pool_entries
from app.domain.swing.trading.pool_state import AccountSnapshot, PoolPosition
from app.domain.swing.trading.strategies.base_ichimoku import BaseIchimokuStrategy as Base
from app.domain.swing.trading.strategies.ichimoku_strategy import IchimokuStrategy
from app.domain.swing.trading.strategies.single_ema_strategy import SingleEMAStrategy
from app.domain.swing.trading.trading_strategy_factory import TradingStrategyFactory

REQUIRED_KEYS = {"이름", "요약", "매수조건", "매수차단", "매수수량", "청산", "매도후", "잘맞는종목", "안맞는종목"}


def cache(**kw):
    base = {"ichi_tenkan": 105.0, "ichi_kijun": 100.0, "ichi_top": 95.0, "ichi_bot": 90.0, "ichi_state": True,
            "ichi_entry": True, "ichi_exit": False, "atr": 3.0, "close": 110.0, "high": 111.0, "low": 108.0,
            "avg_daily_amount": 1e12, "date": "20261030"}
    base.update(kw)
    return base


class FakeRedis:
    def __init__(self, data=None):
        self.data = data or {}

    async def get(self, k):
        return self.data.get(k)


# ==================== 신호 ====================

class SignalTest(unittest.IsolatedAsyncioTestCase):
    async def entry(self, c, price=112.0):
        return await IchimokuStrategy.check_entry_signal(
            redis_client=None, swing_id=1, symbol="AAA", current_price=Decimal(str(price)), frgn_ntby_qty=0,
            acml_vol=0, prdy_vrss_vol_rate=100.0, prdy_ctrt=0.0, cached_indicators=c)

    async def exit(self, c, price=112.0, **kw):
        return await IchimokuStrategy.check_exit_signal(
            redis_client=None, position_id=1, symbol="AAA", current_price=Decimal(str(price)), entry_price=Decimal("100"),
            frgn_ntby_qty=0, acml_vol=0, cached_indicators=c, signal=1, peak_price=0, mrkt_code="NAS", final_batch=True, **kw)

    async def test_entry_when_state_recently_established(self):
        r = await self.entry(cache())
        assert r["action"] == "BUY" and r["reasons"][0] == "일목 진입"

    async def test_no_entry_without_flag_or_when_price_below_cloud_bottom(self):
        assert await self.entry(cache(ichi_entry=False)) is None
        assert await self.entry(cache(), price=89.0) is None            # 시가가 이미 구름 하단 아래 — 사자마자 청산 조건
        assert await self.entry({}) is None

    async def test_exit_only_on_cloud_bottom_close(self):
        r = await self.exit(cache(ichi_exit=True, close=88.0))
        assert r["action"] == "SELL" and r["reasons"][0] == "구름 하단 이탈"
        assert (await self.exit(cache(ichi_exit=False)))["action"] == "HOLD"
        assert (await self.exit(cache(), price=50.0))["action"] == "HOLD"     # 장중 급락만으로는 팔지 않는다 (종가 기준)

    async def test_no_partial_take_profit_and_equal_conviction(self):
        assert await IchimokuStrategy.check_partial_take_profit("AAA", Decimal("200"), Decimal("100"), 1) is None
        assert IchimokuStrategy.calc_conviction(50, 3) == IchimokuStrategy.calc_conviction(0, -3) == 1.0


class CacheLoaderTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_cache_and_fills_optional_defaults(self):
        data = cache()
        data.pop("avg_daily_amount")
        data.pop("date")
        out = await IchimokuStrategy.get_cached_indicators(FakeRedis({"indicators:AAA": json.dumps(data)}), "AAA")
        assert out["avg_daily_amount"] == 0.0 and out["date"] is None and out["ichi_bot"] == 90.0

    async def test_none_when_missing_or_required_key_absent(self):
        assert await IchimokuStrategy.get_cached_indicators(FakeRedis(), "AAA") is None
        data = cache()
        data.pop("ichi_bot")                                                 # EMA 만 있는 낡은 캐시 — 판단하지 않는다
        assert await IchimokuStrategy.get_cached_indicators(FakeRedis({"indicators:AAA": json.dumps(data)}), "AAA") is None


# ==================== 팩토리 · 설명 · 훅 기본값 ====================

class RegistrationTest(unittest.TestCase):
    def test_factory_and_description(self):
        assert TradingStrategyFactory.get_strategy("I") is IchimokuStrategy
        assert TradingStrategyFactory.get_strategy("S") is SingleEMAStrategy
        desc = TradingStrategyFactory.describe_all()
        assert "I" in desc and REQUIRED_KEYS - set(desc["I"]) == set()
        assert "총자산" in desc["I"]["매수수량"]
        assert "검증 미통과" in desc["I"]["안맞는종목"]                         # 근거 없는 낙관을 AI 에 전하지 않는다

    def test_description_follows_constants(self):
        with patch.object(IchimokuStrategy, "RECENT_DAYS", 7):
            assert "최근 7거래일" in IchimokuStrategy.describe()["매수조건"][2]

    def test_hook_defaults_preserve_single_ema_behavior(self):
        for attr, ema, ichi in (("ENRICH_REALTIME_INDICATORS", True, False), ("COOLDOWN_AFTER_EXIT", True, False),
                                ("USES_ENTRY_FILTERS", True, False)):
            assert getattr(SingleEMAStrategy, attr) is ema, attr
            assert getattr(IchimokuStrategy, attr) is ichi, attr
        assert SingleEMAStrategy.initial_stop(100.0, {"atr": 3}) is None             # None = 풀의 기본(EMA 청산선) 공식
        assert SingleEMAStrategy.position_stop({"atr": 3}, 100.0, 110.0) is None

    def test_initial_stop_is_cloud_bottom_only_when_below_price(self):
        assert IchimokuStrategy.initial_stop(110.0, cache()) == 90.0
        assert IchimokuStrategy.initial_stop(80.0, cache()) is None
        assert IchimokuStrategy.initial_stop(110.0, {}) is None
        assert IchimokuStrategy.position_stop(cache(), 100.0, 120.0) == 90.0


class ResetCycleTest(unittest.TestCase):
    def make(self, t):
        s = SwingTrade.create("acc", "NAS", "AAA", t)
        s.SIGNAL, s.HOLD_QTY, s.ENTRY_PRICE = 1, 10, Decimal("100")
        return s

    def test_default_cooldown_is_signal_3_and_ichimoku_goes_straight_to_0(self):
        a, b = self.make("S"), self.make("I")
        batch._reset_cycle(a)
        batch._reset_cycle(b)
        assert a.SIGNAL == 3 and b.SIGNAL == 0
        assert a.HOLD_QTY == b.HOLD_QTY == 0 and a.ENTRY_PRICE is None and b.ENTRY_PRICE is None
        c = self.make("S")
        c.reset_cycle()                                                      # 기존 호출 방식도 그대로 SIGNAL 3
        assert c.SIGNAL == 3


# ==================== 오케스트레이터 ====================

class HandlerTest(unittest.IsolatedAsyncioTestCase):
    def swing(self, signal=0, qty=0):
        return SimpleNamespace(SWING_ID=7, ACCOUNT_NO="acc", MRKT_CODE="NAS", SWING_TYPE="I", SIGNAL=signal,
                               HOLD_QTY=qty, ENTRY_PRICE=Decimal("100") if qty else None, PEAK_PRICE=Decimal("100") if qty else None)

    async def test_waiting_adds_candidate_with_cloud_bottom_stop_and_skips_ema_filters(self):
        bucket = []
        guards = AsyncMock(return_value=True)                                # 호출되면 매수를 막는 필터 — 일목은 호출하지 않아야 한다
        with patch.object(batch, "_defer_reentry", guards), patch.object(batch, "_entry_blocked", guards), \
                patch.object(batch, "_handle_recovery", AsyncMock()) as recovery:
            await batch._handle_waiting(
                self.swing(), IchimokuStrategy, None, None, "u1", "AAA", Decimal("112"), 0, 0, 100.0, 0.0,
                cache(), 1e12, mrkt_code="NAS", pool_bucket=bucket, final_batch=False)
        assert len(bucket) == 1 and bucket[0].initial_stop == 90.0 and bucket[0].conviction == 1.0
        guards.assert_not_called()
        recovery.assert_not_called()

    async def test_waiting_without_signal_does_not_try_recovery_for_ichimoku(self):
        with patch.object(batch, "_handle_recovery", AsyncMock()) as recovery:
            await batch._handle_waiting(
                self.swing(), IchimokuStrategy, None, None, "u1", "AAA", Decimal("112"), 0, 0, 100.0, 0.0,
                cache(ichi_entry=False), 1e12, mrkt_code="NAS", pool_bucket=[], final_batch=False)
        recovery.assert_not_called()

    async def test_ema_waiting_still_uses_filters(self):
        """단일 EMA 의 기본 경로는 그대로 — 신호가 있으면 종가 확인 재진입·가드를 거친다"""
        class Strat(SingleEMAStrategy):
            @classmethod
            async def check_entry_signal(cls, **kw):
                return {"action": "BUY", "price": kw["current_price"], "reasons": ["EMA돌파"]}
        defer, blocked = AsyncMock(return_value=False), AsyncMock(return_value=True)
        with patch.object(batch, "_defer_reentry", defer), patch.object(batch, "_entry_blocked", blocked):
            bucket = []
            await batch._handle_waiting(
                self.swing(), Strat, None, None, "u1", "AAA", Decimal("112"), 0, 0, 100.0, 0.0, {"atr": 3}, 1e12,
                mrkt_code="NAS", pool_bucket=bucket, final_batch=False)
        defer.assert_awaited_once()
        blocked.assert_awaited_once()
        assert bucket == []                                                  # 가드가 막았다

    async def test_position_sells_on_exit_flag_and_holds_otherwise(self):
        sell = AsyncMock()
        with patch.object(batch, "_execute_full_sell", sell):
            await batch._handle_position(
                self.swing(1, 10), IchimokuStrategy, None, None, "u1", "AAA", Decimal("85"), 0, 0,
                cache(ichi_exit=True, close=88.0), mrkt_code="NAS")
            sell.assert_awaited_once()
            sell.reset_mock()
            await batch._handle_position(
                self.swing(1, 10), IchimokuStrategy, None, None, "u1", "AAA", Decimal("85"), 0, 0,
                cache(ichi_exit=False), mrkt_code="NAS")
            sell.assert_not_awaited()


# ==================== 풀 사이징 ====================

BASE_POOL = dict(risk_pct="0.0010", max_positions=30, max_position_pct="0.10", max_open_risk_pct="0.08")


class PoolSizingTest(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_with_stop_is_sized_without_atr(self):
        """전략이 손절선을 주면 ATR 이 없어도 그 손절선(구름 하단)으로 사이징한다 — 거리가 멀수록 수량이 준다"""
        setting = PoolSetting.create("acc", "US", use_yn="Y", **BASE_POOL)
        calls = []

        async def buy(c, qty):
            calls.append((c.st_code, qty))
            return pa.BuyOutcome(pa.BUY_FILLED, qty, c.price)

        mk = lambda code, stop: EntryCandidate(swing_id=1, user_id="u", account_no="acc", st_code=code, mrkt_code="NAS",
                                               price=100.0, conviction=1.0, atr=0.0, avg_daily_amount=1e12, initial_stop=stop)
        await run_pool_entries(setting, [mk("NEAR", 98.0), mk("FAR", 90.0)], AccountSnapshot("US", 1_000_000.0, 1_000_000.0), [], buy)
        got = dict(calls)
        assert got["NEAR"] == 500 and got["FAR"] == 100                      # 리스크 1,000원 ÷ 주당 손절폭 2원 / 10원 (종목당 상한 10만 원=1,000주 안쪽)
        assert got["NEAR"] > got["FAR"]

    async def test_candidate_without_stop_and_atr_is_still_skipped(self):
        setting = PoolSetting.create("acc", "US", use_yn="Y", **BASE_POOL)
        c = EntryCandidate(swing_id=1, user_id="u", account_no="acc", st_code="X", mrkt_code="NAS", price=100.0,
                           conviction=1.0, atr=0.0, avg_daily_amount=1e12)
        result = await run_pool_entries(setting, [c], AccountSnapshot("US", 1_000_000.0, 1_000_000.0), [], AsyncMock())
        assert result.skipped[pa.SKIP_NO_ATR] == 1

    def test_position_uses_own_stop_for_open_risk(self):
        ema = PoolPosition("A", 10, 100.0, 120.0, 3.0)
        ichi = PoolPosition("B", 10, 100.0, 120.0, 3.0, stop=90.0)
        assert ichi.exit_line == 90.0 and ema.exit_line != 90.0
        assert ichi.open_risk == risk_sizing.position_open_risk(100.0, 90.0, 10)


class LoadPositionsTest(unittest.IsolatedAsyncioTestCase):
    async def test_ichimoku_holding_gets_cloud_bottom_stop_and_ema_keeps_formula(self):
        class Repo:
            async def find_holdings(self, account_no, overseas):
                return [SimpleNamespace(ST_CODE="I1", ENTRY_PRICE=100, PEAK_PRICE=110, HOLD_QTY=5, SWING_TYPE="I"),
                        SimpleNamespace(ST_CODE="S1", ENTRY_PRICE=100, PEAK_PRICE=110, HOLD_QTY=5, SWING_TYPE="S")]
        redis = FakeRedis({"indicators:I1": json.dumps(cache()),
                           "indicators:S1": json.dumps({k: 1.0 for k in SingleEMAStrategy.REQUIRED_CACHE_KEYS} | {"atr": 2.0, "obv_recent_diffs": [1.0]})})
        positions = await ps.load_positions(Repo(), redis, "acc", "US")
        i, s = positions
        assert i.stop == 90.0 and i.atr == 3.0
        assert s.stop is None and s.atr == 2.0


# ==================== 지표 캐시 ====================

class CachePayloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_calculate_indicators_adds_ichimoku_values_and_keeps_ema_keys(self):
        from app.domain.swing import service as svc
        n = 700
        rng = np.random.default_rng(2)
        close = 100 * np.exp(np.cumsum(rng.normal(0.0008, 0.015, n)))
        open_ = close * (1 + rng.normal(0, 0.004, n))
        rows = [{"STCK_BSOP_DATE": d, "STCK_OPRC": float(o), "STCK_HGPR": float(max(o, c) * 1.004), "STCK_LWPR": float(min(o, c) * 0.996),
                 "STCK_CLPR": float(c), "ACML_VOL": 1_000_000.0}
                for d, o, c in zip(pd.bdate_range("2023-01-02", periods=n).strftime("%Y%m%d"), open_, close)]

        class FakeStock:
            def __init__(self, db):
                pass

            async def get_stock_history(self, m, c, start):
                return rows

        with patch.object(svc, "StockService", FakeStock):
            data = await svc.SwingService(db=None)._calculate_indicators("NAS", "AAA")
        assert data is not None
        for k in ("ema20", "atr", "obv", "avg_daily_amount", "sma200"):
            assert k in data                                                  # 기존 EMA 키는 그대로
        for k in IchimokuStrategy.REQUIRED_CACHE_KEYS:
            assert k in data and data[k] is not None, k
        df = pd.DataFrame(rows)
        assert data["ichi_bot"] == Base.snapshot(df)["ichi_bot"]
        assert data["ichi_entry"] == Base.snapshot(df)["ichi_entry"]


# ==================== API: 전략 목록 · 타입 변경 검증 ====================

class StrategyApiTest(unittest.TestCase):
    def test_strategy_list_contains_ichimoku_and_single_ema_with_backtest_flag(self):
        from app.domain.swing.service import SwingService
        items = {x["SWING_TYPE"]: x for x in SwingService.list_strategies()}
        assert {"S", "I"} <= set(items)
        assert items["I"]["NAME"] == "일목균형표" and items["I"]["BACKTEST"] is True and items["S"]["BACKTEST"] is True
        assert "검증 미통과" in items["I"]["CAUTION"]
        assert all({"NAME", "SUMMARY", "ENTRY", "EXIT", "SIZING", "CAUTION"} <= set(x) for x in items.values())

    def test_type_change_validation(self):
        from app.domain.swing.service import SwingService
        from app.exceptions import BusinessRuleError, ValidationError
        flat = SimpleNamespace(SWING_TYPE="S", SIGNAL=0, HOLD_QTY=0)
        held = SimpleNamespace(SWING_TYPE="S", SIGNAL=1, HOLD_QTY=5)
        momentum = SimpleNamespace(SWING_TYPE="M", SIGNAL=0, HOLD_QTY=0)
        SwingService._validate_type_change(flat, {"SWING_TYPE": "I"})              # 대기 중이면 바꿀 수 있다
        SwingService._validate_type_change(flat, {"USE_YN": "Y"})                  # 타입을 안 바꾸면 검증하지 않는다
        SwingService._validate_type_change(held, {"SWING_TYPE": "S"})              # 같은 값은 통과
        for swing, new, exc in ((flat, "Z", ValidationError), (flat, "M", ValidationError), (flat, "B", ValidationError),
                                (held, "I", BusinessRuleError), (momentum, "I", BusinessRuleError)):
            with self.assertRaises(exc, msg=(swing.SWING_TYPE, new)):
                SwingService._validate_type_change(swing, {"SWING_TYPE": new})
