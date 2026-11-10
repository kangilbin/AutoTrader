"""
종가 확인 매매 — 손절은 마지막 배치에서 확인하고(장중엔 안전망만), 재진입은 마지막 배치에서만 산다

실행: uv run pytest tests/test_close_confirm.py -v

구성
  1) 순수 함수: 마지막 배치 시각 판정, 청산 판정(decide_exit)
  2) 배치 시나리오: 실제 엔티티·전략·배치를 돌리고 DB/Redis/KIS 만 가짜로 대체
근거 사례: FOUR 2026-11-05 (장중 청산선 이탈로 매도 → 같은 날 종가는 청산선 위), SNOW 2026-10-28 (장중 재진입 → 종가에서 깨짐)
"""
import json
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.domain.swing.trading import auto_swing_batch as batch
from app.domain.swing.trading import close_confirm as cc
from tests import test_order_reconciliation as rec
from tests.test_swing_trade_scenarios import FakeTradeService, Market, Row, SwingScenarioBase

NY, SEOUL = ZoneInfo("America/New_York"), ZoneInfo("Asia/Seoul")


class FinalBatchTimeTest(unittest.TestCase):
    def test_us_final_batch_is_last_two_runs_1550_to_1600_et(self):
        """마지막 두 번 실행(15:50·15:55)이 마지막 배치 구간이다"""
        self.assertTrue(cc.is_final_batch("NAS", datetime(2026, 11, 5, 15, 50, tzinfo=NY)))
        self.assertTrue(cc.is_final_batch("NAS", datetime(2026, 11, 5, 15, 55, tzinfo=NY)))
        self.assertTrue(cc.is_final_batch("NYS", datetime(2026, 11, 5, 15, 59, tzinfo=NY)))
        self.assertFalse(cc.is_final_batch("NAS", datetime(2026, 11, 5, 15, 45, tzinfo=NY)), "세 번째 전 배치는 아님")
        self.assertFalse(cc.is_final_batch("NAS", datetime(2026, 11, 5, 16, 0, tzinfo=NY)))
        self.assertFalse(cc.is_final_batch("NAS", datetime(2026, 11, 5, 10, 0, tzinfo=NY)))

    def test_kr_final_batch_is_1510_to_1520_kst_excluding_closing_auction(self):
        """국내 trade_job 은 15:20 까지 돈다 (scheduler.py) — 15:20 은 종가 동시호가(체결 15:30)라 마지막 실행은 15:15"""
        self.assertTrue(cc.is_final_batch("J", datetime(2026, 11, 5, 15, 10, tzinfo=SEOUL)))
        self.assertTrue(cc.is_final_batch("J", datetime(2026, 11, 5, 15, 15, tzinfo=SEOUL)))
        self.assertFalse(cc.is_final_batch("J", datetime(2026, 11, 5, 15, 5, tzinfo=SEOUL)))
        self.assertFalse(cc.is_final_batch("J", datetime(2026, 11, 5, 15, 20, tzinfo=SEOUL)), "동시호가는 체결이 안 되는 구간")

    def test_weekend_is_never_final_batch(self):
        self.assertFalse(cc.is_final_batch("NAS", datetime(2026, 11, 7, 15, 55, tzinfo=NY)))   # 토요일

    def test_dst_is_handled_by_eastern_time(self):
        """겨울(EST)·여름(EDT) 모두 현지 15:50~16:00 가 마지막 배치 구간이다 — UTC 로 환산해 비교하지 않는다"""
        self.assertTrue(cc.is_final_batch("NAS", datetime(2026, 1, 14, 15, 55, tzinfo=NY)))
        self.assertTrue(cc.is_final_batch("NAS", datetime(2026, 7, 15, 15, 55, tzinfo=NY)))
        utc = datetime(2026, 11, 5, 20, 55, tzinfo=ZoneInfo("UTC"))      # 겨울: 15:55 ET
        self.assertTrue(cc.is_final_batch("NAS", utc))
        self.assertFalse(cc.is_final_batch("NAS", datetime(2026, 7, 15, 20, 55, tzinfo=ZoneInfo("UTC"))))   # 여름 20:55 UTC = 16:55 ET

    def test_trading_date_is_market_local(self):
        late_utc = datetime(2026, 11, 6, 2, 0, tzinfo=ZoneInfo("UTC"))   # 미국 11/05 21:00
        self.assertEqual(cc.trading_date("NAS", late_utc), date(2026, 11, 5))
        self.assertEqual(cc.trading_date("J", late_utc), date(2026, 11, 6))


class PendingStateTest(unittest.IsolatedAsyncioTestCase):
    async def test_reservation_ttl_outlasts_long_holiday(self):
        """예약은 추석·설 같은 긴 연휴(최대 약 9일)를 넘겨도 남아야 다음 거래일에 매도된다"""
        calls = []

        class Stub:
            async def setex(self, key, ttl, value):
                calls.append((key, ttl, value))

        await cc.set_pending(Stub(), 7, date(2026, 11, 5))
        self.assertEqual(calls, [("pending_exit:7", cc.PENDING_EXIT_TTL, "2026-11-05")])
        self.assertGreaterEqual(cc.PENDING_EXIT_TTL, 10 * 86400)

    async def test_bytes_and_bad_values_are_parsed_safely(self):
        class Stub:
            def __init__(self, v): self.v = v
            async def get(self, key): return self.v

        self.assertEqual(await cc.get_pending(Stub(b"2026-11-05"), 1), date(2026, 11, 5))
        self.assertEqual(await cc.get_pending(Stub("2026-11-05"), 1), date(2026, 11, 5))
        self.assertIsNone(await cc.get_pending(Stub("garbage"), 1))
        self.assertIsNone(await cc.get_pending(Stub(None), 1))


class DecideExitTest(unittest.TestCase):
    TODAY = date(2026, 11, 5)
    LINE, ATR = 38.99, 2.14          # FOUR 11/05 (안전망 = 38.99 − 2.14 = 36.85)

    def decide(self, price, final=False, pending=None):
        return cc.decide_exit(price, self.LINE, self.ATR, final_batch=final, pending_date=pending, today=self.TODAY)

    def test_intraday_touch_holds(self):
        """FOUR 10:05 — 청산선 아래(38.05)지만 안전망(36.85) 위 → 장중에는 보유"""
        d = self.decide(38.05)
        self.assertEqual((d.action, d.pending), (cc.HOLD, None))

    def test_safety_net_sells_immediately_in_session(self):
        d = self.decide(36.80)
        self.assertEqual((d.action, d.reason), (cc.SELL, cc.REASON_SAFETY_NET))

    def test_safety_net_boundary_is_inclusive(self):
        self.assertEqual(self.decide(36.85).action, cc.SELL)
        self.assertEqual(self.decide(36.86).action, cc.HOLD)

    def test_final_batch_below_line_reserves_sale(self):
        d = self.decide(38.50, final=True)
        self.assertEqual((d.action, d.pending), (cc.HOLD, True))

    def test_final_batch_above_line_clears_reservation(self):
        """FOUR 15:55 — 종가가 청산선 위(약 42)로 회복 → 예약 없음/해제"""
        d = self.decide(42.00, final=True, pending=None)
        self.assertEqual((d.action, d.pending), (cc.HOLD, False))

    def test_yesterday_reservation_sells_even_if_price_recovered(self):
        """어제 마지막 배치에서 이탈이 확인됐으면 오늘 첫 배치에서 판다 (백테스트의 '다음 날 시가 매도')"""
        d = self.decide(40.00, pending=self.TODAY - timedelta(days=1))
        self.assertEqual((d.action, d.reason), (cc.SELL, cc.REASON_NEXT_DAY))

    def test_reservation_made_today_does_not_sell_today(self):
        d = self.decide(38.50, pending=self.TODAY)
        self.assertEqual(d.action, cc.HOLD)

    def test_safety_net_wins_over_reservation(self):
        d = self.decide(36.0, pending=self.TODAY - timedelta(days=1))
        self.assertEqual(d.reason, cc.REASON_SAFETY_NET)

    def test_sell_decisions_do_not_touch_reservation(self):
        """매도가 부분 체결·실패해도 다음 배치가 같은 판정을 이어가야 한다 — 예약은 매도 완료 후 호출부가 지운다"""
        self.assertIsNone(self.decide(36.0).pending)
        self.assertIsNone(self.decide(40.0, pending=self.TODAY - timedelta(days=1)).pending)

    def test_far_above_line_in_session_does_nothing(self):
        d = self.decide(44.0)
        self.assertEqual((d.action, d.pending), (cc.HOLD, None))

    def test_pending_survives_non_final_batches(self):
        """예약 후 같은 날 장중 배치에서는 예약을 건드리지 않는다 (None)"""
        self.assertIsNone(self.decide(40.0, pending=self.TODAY).pending)


class CloseConfirmScenarioTest(SwingScenarioBase):
    """배치 전체 흐름 — 예약·다음 날 매도·안전망·재진입 보류"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # 기반 클래스가 꺼 둔 종가 확인 모드를 켜고, 거래일을 테스트가 제어한다
        self.today = date(2026, 11, 5)
        for p in (patch.object(cc, "enabled", return_value=True),
                  patch.object(cc, "trading_date", side_effect=lambda *a, **k: self.today)):
            p.start()
            self.addCleanup(p.stop)

    async def cycle(self, final=False, n=1):
        for _ in range(n):
            await self.run_cycle(final_batch=final)

    async def hold_and_line(self):
        """진입 후 (진입가, 청산선, 안전망)"""
        await self.enter_position()
        entry = self.entry_price()
        line = max(entry - 2 * 2.0, entry * 0.93)      # ATR 2.0 (캐시)
        return entry, line, line - 2.0

    # ---------- 손절 ----------

    async def test_intraday_touch_does_not_sell(self):
        entry, line, net = await self.hold_and_line()
        Market.move(line - 1.0)                       # 청산선 아래, 안전망 위
        await self.cycle()
        self.assertEqual(len(Market.sells()), 0, "장중 청산선 터치만으로 팔았다")
        self.assertEqual(self.swing.SIGNAL, 1)
        self.assertNotIn("pending_exit:1", self.redis.store)

    async def test_final_batch_below_line_reserves_then_next_day_sells(self):
        """FOUR 형 — 마지막 배치 이탈 → 예약 → 다음 날 첫 배치에서 (가격이 회복했어도) 매도"""
        entry, line, net = await self.hold_and_line()
        Market.move(line - 1.0)
        await self.cycle(final=True)
        self.assertEqual(len(Market.sells()), 0, "마지막 배치에서 바로 팔았다 (다음 날 매도여야 함)")
        self.assertEqual(self.redis.store.get("pending_exit:1"), "2026-11-05")

        self.today = date(2026, 11, 6)
        Market.move(line + 2.0)
        await self.cycle()
        self.assertEqual(len(Market.sells()), 1)
        self.assertEqual(self.swing.SIGNAL, 3)
        self.assertNotIn("pending_exit:1", self.redis.store, "매도 후 예약이 남았다")
        self.assertEqual(self.redis.store.get("last_exit:1"), "2026-11-06", "재진입 판정용 마지막 청산일 미기록")
        reasons = FakeTradeService.of("S")[0]["reasons"]
        self.assertIn(cc.REASON_NEXT_DAY, reasons)

    async def test_final_batch_recovery_clears_reservation_and_keeps_position(self):
        """FOUR 11/05 그대로 — 장중 이탈, 마지막 배치에서는 회복 → 보유"""
        entry, line, net = await self.hold_and_line()
        Market.move(line - 1.0)
        await self.cycle(final=True)
        self.assertIn("pending_exit:1", self.redis.store)
        Market.move(line + 4.0)
        await self.cycle(final=True)                   # 같은 배치 시각의 마지막 값이 최종
        self.assertNotIn("pending_exit:1", self.redis.store)

        self.today = date(2026, 11, 6)
        await self.cycle()
        self.assertEqual(len(Market.sells()), 0)
        self.assertEqual(self.swing.SIGNAL, 1)

    async def test_same_day_later_batch_does_not_sell_reservation(self):
        entry, line, net = await self.hold_and_line()
        Market.move(line - 1.0)
        await self.cycle(final=True)
        await self.cycle(final=False)                  # 같은 날 이어지는 배치
        self.assertEqual(len(Market.sells()), 0)

    async def test_safety_net_sells_immediately(self):
        entry, line, net = await self.hold_and_line()
        Market.move(net - 1.0)                         # 청산선 − ATR 아래 (큰 폭락)
        await self.cycle()
        self.assertEqual(len(Market.sells()), 1)
        self.assertEqual(self.swing.SIGNAL, 3)
        self.assertIn(cc.REASON_SAFETY_NET, FakeTradeService.of("S")[0]["reasons"])

    async def test_disabled_flag_restores_immediate_sell(self):
        entry, line, net = await self.hold_and_line()
        with patch.object(cc, "enabled", return_value=False):
            Market.move(line - 1.0)
            await self.cycle()
        self.assertEqual(len(Market.sells()), 1, "플래그를 끄면 예전처럼 터치 즉시 매도여야 한다")

    async def test_full_sell_records_last_exit_even_without_reservation(self):
        """안전망 매도도 마지막 청산일을 남긴다 (재진입 판정 입력)"""
        entry, line, net = await self.hold_and_line()
        Market.move(net - 1.0)
        await self.cycle()
        self.assertEqual(self.redis.store.get("last_exit:1"), "2026-11-05")

    async def test_second_final_run_decides_when_runs_disagree(self):
        """마지막 두 번 실행의 값이 다르면 나중 값이 최종 — 첫 실행은 위, 둘째 실행은 아래이면 예약"""
        entry, line, net = await self.hold_and_line()
        Market.move(line + 1.0)
        await self.cycle(final=True)                   # 15:50 — 청산선 위
        self.assertNotIn("pending_exit:1", self.redis.store)
        Market.move(line - 1.0)
        await self.cycle(final=True)                   # 15:55 — 청산선 아래
        self.assertEqual(self.redis.store.get("pending_exit:1"), "2026-11-05")

    async def test_reservation_survives_when_second_final_run_is_missing(self):
        """첫 실행에서 예약했고 둘째 실행이 빠져도(지연·실패) 예약은 남아 다음 거래일에 매도한다"""
        entry, line, net = await self.hold_and_line()
        Market.move(line - 1.0)
        await self.cycle(final=True)                   # 15:50 만 실행
        self.today = date(2026, 11, 6)
        await self.cycle()
        self.assertEqual(len(Market.sells()), 1)

    async def test_due_reservation_sells_even_without_atr(self):
        """지표(ATR)가 비어 있어도 이미 확정된 예약 매도는 실행한다"""
        entry, line, net = await self.hold_and_line()
        self.redis.store["pending_exit:1"] = "2026-11-05"
        self.today = date(2026, 11, 6)
        cache = json.loads(self.redis.store["indicators:AAPL"])
        cache["atr"] = 0
        self.redis.store["indicators:AAPL"] = json.dumps(cache)
        await self.cycle()
        self.assertEqual(len(Market.sells()), 1, "ATR 없음 때문에 예약된 매도가 막혔다")
        self.assertNotIn("pending_exit:1", self.redis.store)

    async def test_no_atr_without_reservation_still_skips(self):
        """예약이 없으면 ATR 없음은 예전처럼 청산 체크를 건너뛴다"""
        entry, line, net = await self.hold_and_line()
        cache = json.loads(self.redis.store["indicators:AAPL"])
        cache["atr"] = 0
        self.redis.store["indicators:AAPL"] = json.dumps(cache)
        Market.move(line - 1.0)
        await self.cycle(final=True)
        self.assertEqual(len(Market.sells()), 0)
        self.assertNotIn("pending_exit:1", self.redis.store)

    async def test_delayed_full_fill_clears_reservation_and_records_last_exit(self):
        """체결 미확인으로 남은 전량 매도가 다음 사이클에 확인되는 경로도 예약을 지우고 청산일을 남긴다"""
        entry, line, net = await self.hold_and_line()
        Market.fill_steps = [0.0, 0.0, 1.0]            # 매도 확인 2회 미체결 → 다음 사이클 재확인에서 전량 체결
        self.redis.store["pending_exit:1"] = "2026-11-04"
        Market.move(net - 1.0)
        await self.cycle()
        self.assertIn("pending_order:1", self.redis.store, "체결 미확인 상태가 아님")
        self.assertIn("pending_exit:1", self.redis.store)

        Market.move(entry)
        await self.cycle()
        self.assertEqual(self.swing.SIGNAL, 3)
        self.assertNotIn("pending_exit:1", self.redis.store, "지연 체결 경로에서 예약이 남았다")
        self.assertEqual(self.redis.store.get("last_exit:1"), "2026-11-05", "지연 체결 경로에서 청산일이 빠졌다")

    async def test_new_entry_discards_stale_reservation(self):
        """청산 경로가 놓친 예약이 남아 있어도 새 포지션이 물려받지 않는다"""
        self.redis.store["pending_exit:1"] = "2026-11-01"
        await self.cycle(final=False, n=2)
        self.assertEqual(self.swing.SIGNAL, 1)
        self.assertNotIn("pending_exit:1", self.redis.store)

    async def test_stale_reservation_is_cleared_while_waiting_or_cooling_down(self):
        """포지션이 없는 상태(대기·쿨다운)의 낡은 예약은 사이클마다 정리된다 — 이후 새 포지션이 물려받지 않는다"""
        for signal in (0, 3, 4):
            self.redis.store["pending_exit:1"] = "2026-11-01"
            self.swing.SIGNAL = signal
            await self.cycle()
            self.assertNotIn("pending_exit:1", self.redis.store, f"SIGNAL {signal} 에서 낡은 예약이 남았다")

    async def test_reservation_is_kept_while_holding(self):
        """보유 중의 예약은 정당한 상태라 건드리지 않는다"""
        entry, line, net = await self.hold_and_line()
        Market.move(line - 1.0)
        await self.cycle(final=True)
        await self.cycle(final=False)
        self.assertEqual(self.redis.store.get("pending_exit:1"), "2026-11-05")

    # ---------- 재진입 ----------

    async def test_first_entry_is_realtime(self):
        """마지막 청산 기록이 없으면 첫 진입 — 장중에 바로 산다"""
        await self.cycle(final=False, n=2)
        self.assertEqual(len(Market.buys()), 1)
        self.assertEqual(self.swing.SIGNAL, 1)

    async def test_reentry_within_window_waits_for_final_batch(self):
        """SNOW 형 — 청산 5일 후 장중 재진입 신호는 보류, 마지막 배치에서 조건이 유지되면 매수"""
        self.redis.store["last_exit:1"] = (self.today - timedelta(days=5)).isoformat()
        await self.cycle(final=False, n=3)
        self.assertEqual(len(Market.buys()), 0, "장중에 재진입했다")
        self.assertEqual(self.swing.SIGNAL, 0)

        await self.cycle(final=True)
        self.assertEqual(len(Market.buys()), 1, "마지막 배치에서 확정 매수가 안 됐다")
        self.assertEqual(self.swing.SIGNAL, 1)

    async def test_reentry_window_boundary(self):
        """30일째까지는 재진입으로 보고, 31일부터는 첫 진입처럼 실시간"""
        self.redis.store["last_exit:1"] = (self.today - timedelta(days=30)).isoformat()
        await self.cycle(final=False, n=2)
        self.assertEqual(len(Market.buys()), 0, "30일째는 아직 재진입")

        self.redis.store["last_exit:1"] = (self.today - timedelta(days=31)).isoformat()
        await self.cycle(final=False, n=1)
        self.assertEqual(len(Market.buys()), 1, "31일째는 첫 진입처럼 바로 사야 한다")

    async def test_reentry_gate_off_when_disabled(self):
        self.redis.store["last_exit:1"] = (self.today - timedelta(days=5)).isoformat()
        with patch.object(cc, "enabled", return_value=False):
            await self.cycle(final=False, n=2)
        self.assertEqual(len(Market.buys()), 1)

    async def test_corrupt_last_exit_is_treated_as_first_entry(self):
        self.redis.store["last_exit:1"] = "not-a-date"
        await self.cycle(final=False, n=2)
        self.assertEqual(len(Market.buys()), 1)


class ReconcileExitStateTest(rec.ReconcileScenarioBase):
    """실보유 대조로 누락 전량 매도를 복구하는 경로도 종가 확인 상태를 정리한다"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.today = date(2026, 11, 5)
        for p in (patch.object(cc, "enabled", return_value=True),
                  patch.object(cc, "trading_date", side_effect=lambda *a, **k: self.today)):
            p.start()
            self.addCleanup(p.stop)

    async def test_recovered_full_sell_clears_reservation_and_records_last_exit(self):
        self.hold_position(qty=50, entry=100.0)
        self.redis.store["pending_exit:1"] = "2026-11-04"
        rec.Broker.add("sell", 50, 96.5, at=rec._kst_now() - rec.dt.timedelta(minutes=30), order_no="0000077777")
        self.broker_position = lambda: {"qty": 0, "avg_price": 0.0, "prpr": 0.0}

        async def reject(user_id, order, db, account_no=None):
            Market.orders.append({"dv": order.ord_dv, "qty": order.qty, "unpr": order.unpr, "account_no": account_no})
            return {"rt_cd": "1", "msg1": "모의투자 잔고내역이 없습니다."}
        rec.oe.foreign_api.place_order_api = reject

        Market.move(80.0)                              # 예약 만기 + 안전망 이하 → 매도 시도 → 거부 → 대조 복구
        await self.run_cycle()

        self.assertEqual(self.swing.SIGNAL, 3)
        self.assertNotIn("pending_exit:1", self.redis.store, "대조 복구 경로에서 예약이 남았다")
        self.assertEqual(self.redis.store.get("last_exit:1"), "2026-11-05", "대조 복구 경로에서 청산일이 빠졌다")


if __name__ == "__main__":
    unittest.main()
