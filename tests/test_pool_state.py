"""
풀 상태(pool_state) 테스트

실행:
  uv run pytest tests/test_pool_state.py -v

풀 매수의 총자산·가용 현금·열린 리스크를 만드는 부분이다. 여기가 틀리면 수량이 틀어지므로
- KIS 응답 필드 선택(D+2 예수금, 총평가금액)과 실패 시 None(매수 보류)
- 열린 리스크 계산(청산선이 본전 이상이면 0, 지표가 없으면 최대 손절폭으로 보수적)
을 고정한다. 응답 숫자는 모의 계좌에서 읽은 실제 값(2026-10-31)이다.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.swing.trading import pool_state as ps
from app.domain.swing.trading.pool_state import AccountSnapshot, PoolPosition


def pos(entry=100.0, peak=100.0, atr=2.0, qty=10, code="X"):
    return PoolPosition(code=code, qty=qty, entry_price=entry, peak_price=peak, atr=atr)


class OpenRiskTest(unittest.TestCase):
    def test_initial_stop_uses_atr(self):
        """진입 직후 청산선 = max(진입가−ATR×2, 진입가−7%) = 96 → 열린 리스크 (100−96)×10"""
        self.assertEqual(pos(atr=2.0).exit_line, 96.0)
        self.assertEqual(pos(atr=2.0).open_risk, 40.0)

    def test_wide_atr_is_capped_by_max_stop(self):
        """ATR 이 커도 최대 손절폭 7%를 넘지 않는다 — 청산선 93"""
        self.assertEqual(pos(atr=10.0).exit_line, 93.0)

    def test_open_risk_zero_once_line_reaches_breakeven(self):
        """고점이 +30% 이상이면 청산선이 본전 위 — 이익 포지션은 한도를 차지하지 않는다"""
        p = pos(peak=140.0, atr=2.0)
        self.assertGreaterEqual(p.exit_line, 100.0)
        self.assertEqual(p.open_risk, 0.0)

    def test_missing_atr_falls_back_to_max_stop_not_zero_risk(self):
        """지표 캐시가 없으면 ATR 0 — 리스크를 0 으로 보지 않고 최대 손절폭 7%로 본다"""
        p = pos(atr=0.0)
        self.assertEqual(p.exit_line, 93.0)
        self.assertEqual(p.open_risk, 70.0)

    def test_compute_pool_state(self):
        snap = AccountSnapshot("KR", equity=1_000_000, cash=400_000)
        state = ps.compute_pool_state(snap, [pos(atr=2.0), pos(peak=140.0), pos(atr=0.0)])
        self.assertEqual((state.equity, state.cash, state.position_count), (1_000_000, 400_000, 3))
        self.assertEqual(state.open_risk, 40.0 + 0.0 + 70.0)
        self.assertAlmostEqual(state.open_risk_pct, 110.0 / 1_000_000)

    def test_empty_pool(self):
        state = ps.compute_pool_state(AccountSnapshot("US", 5000.0, 5000.0), [])
        self.assertEqual((state.position_count, state.open_risk, state.open_risk_pct), (0, 0, 0))


class KrSnapshotTest(unittest.IsolatedAsyncioTestCase):
    async def snapshot(self, output2):
        # 이 클래스는 예수금 경로만 본다 — 매수가능조회는 막아 둔다 (조회는 BuyingPowerSnapshotTest 가 검증)
        with patch.object(ps.kis_api, "get_stock_balance", AsyncMock(return_value={"output1": [], "output2": output2})), \
             patch.object(ps, "_fetch_buying_power", AsyncMock(return_value=None)):
            return await ps.fetch_account_snapshot("u", None, "50000000-01", "KR")

    async def test_real_mock_account_values(self):
        """모의 계좌 실제 응답 — 총자산 = tot_evlu_amt, 현금 = prvs_rcdl_excc_amt"""
        s = await self.snapshot({
            "dnca_tot_amt": "92655313", "prvs_rcdl_excc_amt": "92655313",
            "scts_evlu_amt": "9727800", "tot_evlu_amt": "102383113"})
        self.assertEqual((s.market, s.equity, s.cash), ("KR", 102383113.0, 92655313.0))

    async def test_cash_uses_d2_so_sale_proceeds_count_before_settlement(self):
        """매도 직후: 예수금(D+0)은 그대로인데 D+2 예수금은 매도대금만큼 늘어난다 — D+2 를 쓴다"""
        s = await self.snapshot({
            "dnca_tot_amt": "50000000", "prvs_rcdl_excc_amt": "80000000",
            "scts_evlu_amt": "40000000", "tot_evlu_amt": "120000000"})
        self.assertEqual(s.cash, 80_000_000.0)

    async def test_falls_back_to_d0_cash_when_d2_missing(self):
        s = await self.snapshot({"dnca_tot_amt": "50000000", "scts_evlu_amt": "10000000", "tot_evlu_amt": "60000000"})
        self.assertEqual(s.cash, 50_000_000.0)

    async def test_equity_falls_back_to_cash_plus_holdings(self):
        s = await self.snapshot({"prvs_rcdl_excc_amt": "30000000", "scts_evlu_amt": "5000000"})
        self.assertEqual(s.equity, 35_000_000.0)

    async def test_cash_never_exceeds_equity(self):
        s = await self.snapshot({"prvs_rcdl_excc_amt": "90", "tot_evlu_amt": "100", "dnca_tot_amt": "500"})
        self.assertLessEqual(s.cash, s.equity)

    async def test_unreadable_equity_returns_none(self):
        """총자산을 못 읽으면 None — 0 으로 사이징하지 않고 이번 사이클 풀 매수를 건너뛴다"""
        self.assertIsNone(await self.snapshot({}))
        self.assertIsNone(await self.snapshot({"tot_evlu_amt": "abc", "prvs_rcdl_excc_amt": ""}))


class BuyingPowerSnapshotTest(unittest.IsolatedAsyncioTestCase):
    """현금은 잔고의 예수금이 아니라 '주문가능 금액'(매수가능조회)과 비교한 작은 값 — 모의 실측에서 예수금이 더 컸다"""
    KR_O2 = {"dnca_tot_amt": "92655313", "prvs_rcdl_excc_amt": "92655313", "scts_evlu_amt": "9727800",
             "tot_evlu_amt": "102383113"}
    US_PRESENT = {"dnca_amt": "240094.75", "exrt": "1335.3"}

    async def kr(self, bp, **kw):
        kis = AsyncMock(return_value=bp)
        with patch.object(ps.kis_api, "get_stock_balance", AsyncMock(return_value={"output1": [], "output2": self.KR_O2})), \
             patch.object(ps.kis_api, "get_buying_power", kis):
            return await ps.fetch_account_snapshot("u", None, "ACC", "KR", **kw), kis

    async def us(self, bp, **kw):
        fx = AsyncMock(return_value=bp)
        with patch.object(ps.foreign_api, "get_present_balance", AsyncMock(return_value=self.US_PRESENT)), \
             patch.object(ps.foreign_api, "get_stock_balance",
                          AsyncMock(return_value={"output1": [{"pdno": "UNH", "evlu_amt": "10600.72"}], "output2": {}})), \
             patch.object(ps.foreign_api, "get_buying_power", fx):
            return await ps.fetch_account_snapshot("u", None, "ACC", "US", **kw), fx

    async def test_kr_cash_is_the_smaller_of_deposit_and_orderable_cash(self):
        s, kis = await self.kr({"cash": 92_192_036.0})
        self.assertEqual(s.cash, 92_192_036.0)             # 예수금 92,655,313 보다 작은 주문가능현금
        self.assertEqual(s.equity, 102_383_113.0)           # 총자산은 그대로
        self.assertEqual(kis.await_args.args[2], "459580")  # 금액은 종목과 무관 — 파킹 ETF 코드를 대표로
        self.assertEqual(kis.await_args.kwargs["account_no"], "ACC")

    async def test_kr_orderable_cash_larger_than_deposit_does_not_raise_cash(self):
        s, _ = await self.kr({"cash": 999_999_999.0})
        self.assertEqual(s.cash, 92_655_313.0)

    async def test_us_cash_is_orderable_foreign_currency_not_the_deposit(self):
        """모의 실측: 외화예수금 $240,094.75 vs 주문가능외화 $86,211.80 — 예수금으로 계획하면 주문이 거절된다"""
        s, fx = await self.us({"cash": 86_211.80})
        self.assertAlmostEqual(s.cash, 86_211.80)
        self.assertAlmostEqual(s.equity, 240094.75 + 10600.72)
        self.assertEqual(fx.await_args.args[2:5], ("SGOV", "NYS", 100.0))

    async def test_lookup_failure_falls_back_to_deposit_for_planning_but_strict_refuses(self):
        s, _ = await self.kr(None)
        self.assertEqual(s.cash, 92_655_313.0, "계획용은 예수금으로 대신한다 (주문 단계에서 증권사가 걸러낸다)")
        strict, _ = await self.kr(None, strict=True)
        self.assertIsNone(strict, "돈이 움직이는 판단은 주문가능 금액을 모르면 멈춘다")
        strict_us, _ = await self.us(None, strict=True)
        self.assertIsNone(strict_us)

    async def test_equity_only_callers_skip_the_lookup(self):
        """매도 사유 표시처럼 총자산만 필요한 호출은 매수가능조회를 하지 않는다 (KIS 호출 절약)"""
        s, kis = await self.kr({"cash": 1.0}, with_buying_power=False)
        self.assertEqual(s.cash, 92_655_313.0)
        kis.assert_not_called()


class UsSnapshotTest(unittest.IsolatedAsyncioTestCase):
    async def snapshot(self, present, holdings):
        with patch.object(ps.foreign_api, "get_present_balance", AsyncMock(return_value=present)), \
             patch.object(ps.foreign_api, "get_stock_balance", AsyncMock(return_value={"output1": holdings, "output2": {}})), \
             patch.object(ps, "_fetch_buying_power", AsyncMock(return_value=None)):
            return await ps.fetch_account_snapshot("u", None, "50000000-01", "US")

    async def test_real_mock_account_values(self):
        """모의 계좌 실제 값 — 외화예수금 240,094.75 + UNH 평가 10,600.72"""
        s = await self.snapshot({"dnca_amt": "240094.75", "exrt": "1335.3"}, [{"pdno": "UNH", "evlu_amt": "10600.72000000"}])
        self.assertEqual(s.market, "US")
        self.assertAlmostEqual(s.cash, 240094.75)
        self.assertAlmostEqual(s.equity, 250695.47)

    async def test_no_exchange_rate_means_no_cash_so_none(self):
        """모의 환율 미수신 등 예수금을 산출할 수 없으면 None — 보유 평가만으로 사이징하지 않는다"""
        self.assertIsNone(await self.snapshot(None, [{"evlu_amt": "1000"}]))

    async def test_zero_everything_returns_none(self):
        self.assertIsNone(await self.snapshot({"dnca_amt": "0"}, []))

    async def test_unknown_market_raises(self):
        with self.assertRaises(ValueError):
            await ps.fetch_account_snapshot("u", None, "A", "JP")


class LoadPositionsTest(unittest.IsolatedAsyncioTestCase):
    class FakeRepo:
        def __init__(self, swings):
            self.swings, self.calls = swings, []

        async def find_holdings(self, account_no, overseas):
            self.calls.append((account_no, overseas))
            return self.swings

    def swing(self, code, entry, peak, qty):
        return SimpleNamespace(ST_CODE=code, ENTRY_PRICE=entry, PEAK_PRICE=peak, HOLD_QTY=qty)

    async def test_atr_from_cache_and_conservative_when_missing(self):
        repo = self.FakeRepo([self.swing("A", 100, 120, 10), self.swing("B", 50, None, 4)])
        cache = {"A": {"atr": 2.5}, "B": None}
        with patch.object(ps.SingleEMAStrategy, "get_cached_indicators",
                          AsyncMock(side_effect=lambda r, code: cache[code])):
            positions = await ps.load_positions(repo, None, "acc", "US")
        a, b = positions
        self.assertEqual((a.code, a.qty, a.entry_price, a.peak_price, a.atr), ("A", 10, 100.0, 120.0, 2.5))
        self.assertEqual((b.peak_price, b.atr), (50.0, 0.0))        # 고점이 없으면 진입가, 지표가 없으면 ATR 0
        self.assertEqual(repo.calls, [("acc", True)])               # 미국 풀은 해외 스윙만 조회

    async def test_kr_pool_queries_domestic(self):
        repo = self.FakeRepo([])
        with patch.object(ps.SingleEMAStrategy, "get_cached_indicators", AsyncMock(return_value=None)):
            self.assertEqual(await ps.load_positions(repo, None, "acc", "KR"), [])
        self.assertEqual(repo.calls, [("acc", False)])


class FindHoldingsQueryTest(unittest.IsolatedAsyncioTestCase):
    """find_holdings 의 조회 조건 — 빠지면 낡은 비활성 보유 행이 풀 한도를 영구히 차지하거나 시장이 섞인다"""

    async def sql(self, overseas):
        from app.domain.swing.repository import SwingRepository

        class FakeResult:
            def scalars(self):
                return self

            def all(self):
                return []

        class FakeDB:
            query = None

            async def execute(self, query):
                FakeDB.query = query
                return FakeResult()

        await SwingRepository(FakeDB()).find_holdings("acc-1", overseas)
        return str(FakeDB.query.compile(compile_kwargs={"literal_binds": True}))

    async def test_only_active_holding_swings_of_the_account(self):
        sql = await self.sql(overseas=False)
        self.assertIn('"SWING_TRADE"."ACCOUNT_NO" = \'acc-1\'', sql)
        self.assertIn('"SWING_TRADE"."USE_YN" = \'Y\'', sql)
        self.assertIn('"SWING_TRADE"."HOLD_QTY" > 0', sql)

    async def test_market_split(self):
        us = await self.sql(overseas=True)
        kr = await self.sql(overseas=False)
        self.assertIn('"SWING_TRADE"."MRKT_CODE" IN (\'NYS\', \'NAS\', \'AMS\')', us)
        self.assertIn('"SWING_TRADE"."MRKT_CODE" NOT IN (\'NYS\', \'NAS\', \'AMS\')', kr)


if __name__ == "__main__":
    unittest.main()
