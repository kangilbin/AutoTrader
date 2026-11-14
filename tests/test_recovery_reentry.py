"""
회복 재진입 — 청산 뒤 60일 이내 장기 상승 추세로 복귀하면 신호·OBV 쿨다운 없이 마지막 배치에서 재매수한다

실행: uv run pytest tests/test_recovery_reentry.py -v

구성
  1) 조건 판정(decide): 윈도우·SMA200·EMA20·청산가 경계, 지표/청산가 부족
  2) 청산가 Redis 기록
  3) 실제 배치: 쿨다운(3/4)·대기(0) 상태에서 매수, 마지막 배치 한정, 가드·Q3 우선, 플래그 off
"""
import json
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from app.domain.swing.trading import close_confirm as cc
from app.domain.swing.trading import credit_gate as cg
from app.domain.swing.trading import loss_guard as lg
from app.domain.swing.trading import recovery_reentry as rr
from tests.test_swing_trade_scenarios import FakeRedis, FakeTradeService, Market, SwingScenarioBase, make_indicator_cache

TODAY = date(2026, 11, 12)
EXIT_DAY = TODAY - timedelta(days=10)
UP = dict(close=110.0, ema20=105.0, sma200=100.0, sma200_prev20=95.0, date="20261111", realtime_ema20=106.0)


class DecideTest(unittest.TestCase):
    def decide(self, cached=None, last_exit=EXIT_DAY, price=108.0, today=TODAY, now=111.0):
        return rr.decide({**UP, **(cached or {})}, last_exit, price, today, now)

    def test_all_conditions_met(self):
        self.assertTrue(self.decide().ok)

    def test_no_last_exit(self):
        self.assertFalse(self.decide(last_exit=None).ok)

    def test_window_boundary(self):
        self.assertTrue(self.decide(last_exit=TODAY - timedelta(days=60)).ok)
        self.assertFalse(self.decide(last_exit=TODAY - timedelta(days=61)).ok)
        self.assertFalse(self.decide(last_exit=TODAY + timedelta(days=1)).ok, "미래 청산일은 비정상")

    def test_close_must_be_above_sma200(self):
        self.assertFalse(self.decide({"close": 100.0}).ok, "같으면 위가 아니다")
        self.assertFalse(self.decide({"close": 99.0, "ema20": 90.0}).ok)

    def test_sma200_must_be_rising(self):
        self.assertFalse(self.decide({"sma200_prev20": 100.0}).ok, "같으면 상승이 아니다")
        self.assertFalse(self.decide({"sma200_prev20": 101.0}).ok)

    def test_close_must_be_above_ema20(self):
        self.assertFalse(self.decide({"ema20": 110.0}).ok)
        self.assertFalse(self.decide({"ema20": 111.0}).ok)

    def test_close_must_recover_last_exit_price(self):
        self.assertTrue(self.decide(price=110.0).ok, "같으면 회복")
        self.assertFalse(self.decide(price=110.01).ok)

    def test_bar_must_be_after_exit(self):
        """청산 당일·직후의 캐시 종가는 청산 전 값 — 갭하락 손절 뒤 바로 되사지 않게 막는다"""
        self.assertFalse(self.decide({"date": EXIT_DAY.strftime("%Y%m%d")}, price=94.0).ok, "청산일 봉")
        self.assertFalse(self.decide({"date": (EXIT_DAY - timedelta(days=1)).strftime("%Y%m%d")}, price=94.0).ok)
        self.assertTrue(self.decide({"date": (EXIT_DAY + timedelta(days=1)).strftime("%Y%m%d")}).ok)
        self.assertTrue(self.decide({"date": "2026-11-11"}).ok, "대시 형식도 읽는다")
        self.assertFalse(self.decide({"date": None}).ok)
        self.assertFalse(self.decide({"date": "garbage"}).ok)

    def test_live_price_must_be_above_realtime_ema20(self):
        self.assertFalse(self.decide(now=106.0).ok, "같으면 위가 아니다")
        self.assertFalse(self.decide(now=97.0).ok, "마지막 배치에 추세선 아래로 내려온 날")
        self.assertFalse(self.decide(now=None).ok)
        self.assertFalse(self.decide({"realtime_ema20": None}).ok)

    def test_missing_data_never_enters(self):
        for key in ("close", "ema20", "sma200", "sma200_prev20"):
            self.assertFalse(self.decide({key: None}).ok, key)
            self.assertFalse(self.decide({key: float("nan")}).ok, key)
        self.assertFalse(self.decide(price=None).ok)
        self.assertFalse(self.decide(price=0).ok)
        self.assertFalse(rr.decide({}, EXIT_DAY, 100, TODAY, 100).ok)


class ExitPriceRecordTest(unittest.IsolatedAsyncioTestCase):
    async def test_exit_price_recorded_and_read(self):
        redis = FakeRedis()
        with patch.object(cc, "trading_date", return_value=TODAY):
            await cc.record_full_exit(redis, 3, "J", entry_price=100, exit_price=108.5)
        self.assertEqual(await cc.get_last_exit_price(redis, 3), 108.5)

    async def test_unknown_exit_price_clears_stale_value(self):
        redis = FakeRedis()
        with patch.object(cc, "trading_date", return_value=TODAY):
            await cc.record_full_exit(redis, 3, "J", entry_price=100, exit_price=108.5)
            await cc.record_full_exit(redis, 3, "J")
        self.assertIsNone(await cc.get_last_exit_price(redis, 3))

    async def test_one_failing_write_does_not_skip_loss_streak(self):
        redis = FakeRedis()
        real = redis.setex

        async def flaky(key, ttl, value):
            if key.startswith("last_exit"):
                raise RuntimeError("redis blip")
            await real(key, ttl, value)
        redis.setex = flaky
        with patch.object(cc, "trading_date", return_value=TODAY):
            await cc.record_full_exit(redis, 3, "J", entry_price=100, exit_price=90)
        self.assertEqual(json.loads(redis.store[lg.state_key(3)])["losses"], 1)

    async def test_corrupt_price_is_none(self):
        redis = FakeRedis()
        for raw in ("abc", "-5", "0"):
            redis.store[cc.last_exit_price_key(3)] = raw
            self.assertIsNone(await cc.get_last_exit_price(redis, 3), raw)


class BatchRecoveryTest(SwingScenarioBase):
    """청산 10일 뒤, 신호는 없지만(시세가 EMA20 아래) 전일 기준 장기 상승 추세 복귀 상태"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.today = TODAY
        for p in (patch.object(cc, "enabled", return_value=True),
                  patch.object(cc, "trading_date", side_effect=lambda *a, **k: self.today)):
            p.start()
            self.addCleanup(p.stop)
        self.set_cache()
        self.redis.store["last_exit:1"] = EXIT_DAY.isoformat()
        self.redis.store[cc.last_exit_price_key(1)] = "108.0"
        Market.move(106.5)         # 실시간 EMA20 위 — 연속 확인이 필요한 일반 매수 신호는 첫 사이클에 나오지 않는다
        self.swing.SIGNAL = 3

    def set_cache(self, **over):
        self.redis.store["indicators:AAPL"] = json.dumps(make_indicator_cache(**{**UP, **over}))

    async def test_cooldown_state_buys_in_final_batch(self):
        for signal in (3, 4, 0):
            with self.subTest(signal=signal):
                Market.reset(); Market.move(106.5)
                FakeTradeService.calls = []
                self.swing.SIGNAL, self.swing.HOLD_QTY, self.swing.ENTRY_PRICE = signal, 0, None
                await self.run_cycle(final_batch=True)
                self.assertEqual(len(Market.buys()), 1, f"SIGNAL {signal} 에서 회복 재진입 매수가 없다")
                self.assertEqual(self.swing.SIGNAL, 1)
                reasons = FakeTradeService.of("B")[0].get("reasons") or []
                self.assertTrue(any("회복 재진입" in r for r in reasons), reasons)

    async def test_price_below_realtime_ema20_does_not_buy(self):
        Market.move(100.0)
        await self.run_cycle(final_batch=True)
        self.assertEqual(len(Market.buys()), 0)

    async def test_failure_after_fill_leaves_waiting_state_for_adoption(self):
        """주문이 나간 뒤 예외 → 쿨다운 상태로 남으면 편입이 안 돌아 포지션이 방치된다. 대기(0)로 저장한다"""
        from app.domain.swing.trading import order_executor as oe
        self.swing.SIGNAL = 4

        commits, at_boom = [], []
        real_commit = self.db.commit

        async def counting_commit():
            commits.append(self.swing.SIGNAL)
            await real_commit()
        self.db.commit = counting_commit

        async def boom(*a, **k):
            at_boom.append(len(commits))
            raise RuntimeError("post-fill failure")
        with patch.object(oe.SwingOrderExecutor, "execute_buy", boom):
            await self.run_cycle(final_batch=True)
        # 가짜 DB 의 롤백은 상태를 되돌리지 않으므로, '실패 뒤에 SIGNAL 0 으로 커밋했는가'를 직접 본다
        self.assertGreater(len(commits), at_boom[0], "실패 뒤 대기 상태를 저장(커밋)하지 않았다")
        self.assertEqual(commits[-1], 0)

    async def test_recovery_buy_records_reasons_and_fires_buy_notification(self):
        """체결 이력에는 '회복 재진입'과 판정 사유(청산 후 며칠·청산가)가 남고, 쿨다운에서 사도 매수 완료 알림 조건(0→1)을 만족한다"""
        import json as _json
        from app.domain.swing.trading import auto_swing_batch as batch
        calls = []
        batch._fire_trade_notification = lambda *a, **k: calls.append((a, k))   # asyncSetUp 이 원복한다
        self.swing.SIGNAL = 4
        await self.run_cycle(final_batch=True)

        reasons = FakeTradeService.of("B")[0].get("reasons") or []
        self.assertEqual(reasons[0], "회복 재진입")
        self.assertIn("10일 전 청산(108.00)", reasons[1])
        self.assertLess(len(_json.dumps(reasons, ensure_ascii=False)), 500, "TRADE_REASONS String(500) 초과")
        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(kwargs.get("prev_signal", args[2] if len(args) > 2 else None), 0)
        self.assertEqual(self.swing.SIGNAL, 1)

    async def test_not_in_regular_batch(self):
        await self.run_cycle(final_batch=False)
        self.assertEqual(len(Market.buys()), 0)
        self.assertIn(self.swing.SIGNAL, (3, 4), "회복 재진입은 쿨다운 상태를 매수 없이 풀면 안 된다 (OBV 진행 3→4 는 정상)")

    async def test_weak_trend_does_not_buy_and_keeps_cooldown(self):
        self.set_cache(sma200_prev20=101.0)      # SMA200 하락 중
        await self.run_cycle(final_batch=True)
        self.assertEqual(len(Market.buys()), 0)
        self.assertIn(self.swing.SIGNAL, (3, 4))

    async def test_below_last_exit_price_does_not_buy(self):
        self.redis.store[cc.last_exit_price_key(1)] = "120.0"
        await self.run_cycle(final_batch=True)
        self.assertEqual(len(Market.buys()), 0)

    async def test_outside_window_does_not_buy(self):
        self.redis.store["last_exit:1"] = (TODAY - timedelta(days=61)).isoformat()
        await self.run_cycle(final_batch=True)
        self.assertEqual(len(Market.buys()), 0)

    async def test_loss_guard_has_priority(self):
        self.redis.store[lg.state_key(1)] = json.dumps(
            {"losses": 0, "ban_until": (TODAY + timedelta(days=30)).isoformat()})
        await self.run_cycle(final_batch=True)
        self.assertEqual(len(Market.buys()), 0)

    async def test_credit_gate_blocks_recovery(self):
        end = date.today() - timedelta(days=1)
        vals = (3.11, 3.10, 3.15, 3.08, 3.20, 3.12)      # 5개 전보다 상승 → 차단
        await cg.store_observations(self.redis, [(end - timedelta(days=5 - i), v) for i, v in enumerate(vals)])
        await self.run_cycle(final_batch=True)
        self.assertEqual(len(Market.buys()), 0)

    async def test_disabled_flag(self):
        with patch.object(rr, "enabled", return_value=False):
            await self.run_cycle(final_batch=True)
        self.assertEqual(len(Market.buys()), 0)
        self.assertIn(self.swing.SIGNAL, (3, 4))

    async def test_missing_exit_price_does_not_buy(self):
        del self.redis.store[cc.last_exit_price_key(1)]
        await self.run_cycle(final_batch=True)
        self.assertEqual(len(Market.buys()), 0)

    async def test_unconfirmed_recovery_buy_keeps_cooldown_state(self):
        """주문이 체결 미확인으로 끝나면 메모리에서 풀었던 쿨다운(3/4)을 되돌려 저장한다"""
        from app.domain.swing.trading import order_executor as oe
        self.swing.SIGNAL = 4
        unconfirmed = {"success": True, "unconfirmed": True, "pending_order": {"order_no": "1"}}

        async def fake_buy(*a, **k):
            return unconfirmed
        with patch.object(oe.SwingOrderExecutor, "execute_buy", fake_buy):
            await self.run_cycle(final_batch=True)
        self.assertEqual(self.swing.SIGNAL, 4)

    async def test_without_close_confirm_buys_in_any_batch(self):
        """종가 확인 모드가 꺼져 있으면(마지막 배치 개념 없음) 배치를 가리지 않는다"""
        with patch.object(cc, "enabled", return_value=False):
            await self.run_cycle(final_batch=False)
        self.assertEqual(len(Market.buys()), 1)


if __name__ == "__main__":
    unittest.main()


class SmaCacheTest(unittest.IsolatedAsyncioTestCase):
    """지표 캐시가 SMA200 과 20거래일 전 SMA200 을 계산하고, 전략 캐시 로더가 그대로 통과시킨다"""

    @staticmethod
    def history(n):
        rows = []
        for i in range(n):
            close = 100.0 + i          # 매일 +1 상승 → SMA200 도 상승
            rows.append({"STCK_BSOP_DATE": f"d{i:04d}", "STCK_OPRC": close, "STCK_HGPR": close + 1,
                         "STCK_LWPR": close - 1, "STCK_CLPR": close, "ACML_VOL": 1_000_000 + (i % 7) * 10_000})
        return rows

    async def calc(self, n):
        from app.domain.swing import service as svc

        async def fake_history(self_, mrkt, code, start):
            return SmaCacheTest.history(n)

        with patch.object(svc.StockService, "get_stock_history", fake_history):
            return await svc.SwingService(None)._calculate_indicators("NAS", "AAPL")

    async def test_sma200_and_20_days_ago(self):
        data = await self.calc(260)
        closes = [100.0 + i for i in range(260)]
        self.assertAlmostEqual(data["sma200"], sum(closes[-200:]) / 200)
        self.assertAlmostEqual(data["sma200_prev20"], sum(closes[-220:-20]) / 200)
        self.assertGreater(data["sma200"], data["sma200_prev20"])

    async def test_short_history_gives_none(self):
        data = await self.calc(150)
        self.assertIsNone(data["sma200"])
        self.assertIsNone(data["sma200_prev20"])

    async def test_exactly_200_bars_has_sma_but_no_prev(self):
        data = await self.calc(200)
        self.assertIsNotNone(data["sma200"])
        self.assertIsNone(data["sma200_prev20"])

    async def test_strategy_loader_passes_sma_through_and_tolerates_old_cache(self):
        from app.domain.swing.trading.strategies.single_ema_strategy import SingleEMAStrategy
        redis = FakeRedis()
        redis.store["indicators:AAPL"] = json.dumps(make_indicator_cache(**UP))
        loaded = await SingleEMAStrategy.get_cached_indicators(redis, "AAPL")
        self.assertEqual((loaded["sma200"], loaded["sma200_prev20"]), (100.0, 95.0))
        redis.store["indicators:AAPL"] = json.dumps(make_indicator_cache())     # 구버전 캐시
        loaded = await SingleEMAStrategy.get_cached_indicators(redis, "AAPL")
        self.assertIsNotNone(loaded, "SMA 없는 구캐시 때문에 매매 판단이 막히면 안 된다")
        self.assertIsNone(loaded["sma200"])
