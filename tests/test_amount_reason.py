"""
체결 이력의 금액 비율 문구(_amount_reason) 테스트

실행:
  uv run pytest tests/test_amount_reason.py -v

"투입 33.5%" / "회수 35.2%"는 TRADE_HISTORY 의 매매 사유로 저장된다 (푸시 본문이 아니다).
- 기준(총자산)을 못 읽으면 문구를 생략한다 (체결 이력 저장은 막지 않는다)
- 사유는 총자산 대비로 적는다: 풀 매수는 할당기가 읽은 총자산을 받고,
  매도·재확인·복구는 풀이 켜져 있을 때만 그 시점 잔고를 읽는다
- 어떤 경우에도 문구 계산 실패가 체결 이력 저장을 막으면 안 된다 (None 반환)
"""
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.pool.entity import PoolSetting
from app.domain.pool.repository import PoolRepository
from app.domain.swing.repository import SwingRepository
from app.domain.swing.trading import pool_state
from app.domain.swing.trading.order_executor import SwingOrderExecutor
from app.domain.swing.trading.pool_state import AccountSnapshot

reason = SwingOrderExecutor._amount_reason


def swing(mrkt="NAS"):
    return SimpleNamespace(SWING_ID=1, ACCOUNT_NO="acc", MRKT_CODE=mrkt)


class Env:
    """스윙 조회·풀 설정·KIS 잔고를 가짜로 — 호출 여부도 기록한다"""

    def __init__(self, sw, setting=None, snapshot=None, snapshot_error=None):
        self.find_swing = AsyncMock(return_value=sw)
        self.find_pool = AsyncMock(return_value=setting)
        self.fetch = AsyncMock(return_value=snapshot, side_effect=snapshot_error)

    def __enter__(self):
        SwingOrderExecutor._equity_cache.clear()      # 테스트끼리 30초 캐시가 새지 않게
        self.patches = [
            patch.object(SwingRepository, "find_by_id", self.find_swing),
            patch.object(PoolRepository, "find", self.find_pool),
            patch.object(pool_state, "fetch_account_snapshot", self.fetch),
        ]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()


def active(market="US"):
    return PoolSetting.create("acc", market, use_yn="Y")


class LegacyTest(unittest.IsolatedAsyncioTestCase):
    async def test_pool_off_has_no_basis(self):
        """총자산 기준을 못 읽으면(풀 꺼짐) 문구를 생략한다"""
        with Env(swing()) as e:
            self.assertIsNone(await reason(1, 33_500, None, "투입"))
            self.assertIsNone(await reason(1, 35_200, None, "회수", user_id="u"))
        e.fetch.assert_not_called()          # 풀이 꺼져 있으면 KIS 잔고를 읽지 않는다

    async def test_non_positive_amount_has_no_reason(self):
        with Env(swing()):
            self.assertIsNone(await reason(1, 0, None, "투입"))
            self.assertIsNone(await reason(1, -5, None, "투입"))

    async def test_init_zero_without_user_has_no_reason(self):
        """INIT=0 이고 user_id 도 없으면 기준이 없다 — 이전과 같이 문구 생략"""
        with Env(swing()) as e:
            self.assertIsNone(await reason(1, 1000, None, "투입"))
        e.fetch.assert_not_called()


class ExplicitEquityTest(unittest.IsolatedAsyncioTestCase):
    async def test_pool_buy_uses_given_total_assets_without_any_lookup(self):
        """풀 매수는 할당기가 읽은 총자산을 넘긴다 — DB·KIS 조회 없이 총자산 대비"""
        with Env(swing()) as e:
            self.assertEqual(await reason(1, 25_255, None, "투입", equity=1_000_000), "투입 2.5% (총자산 대비)")
        e.find_swing.assert_not_called()
        e.fetch.assert_not_called()

    async def test_given_equity_wins_over_init_amount(self):
        """INIT 이 남아 있어도 풀 매수는 총자산 기준 — 풀 모드의 의미를 따른다"""
        with Env(swing()):
            self.assertEqual(await reason(1, 25_000, None, "투입", equity=1_000_000), "투입 2.5% (총자산 대비)")


class PoolBasisForAllSwingsTest(unittest.IsolatedAsyncioTestCase):
    async def test_leftover_allocation_in_active_pool_uses_total_assets_for_sell_too(self):
        """풀이 켜지면 매수(총자산 대비)와 같은 기준으로 회수를 적는다 — 비교 가능"""
        with Env(swing(), setting=active("US"), snapshot=AccountSnapshot("US", 1_000_000.0, 1.0)):
            self.assertEqual(await reason(1, 26_000, None, "회수", user_id="u"), "회수 2.6% (총자산 대비)")

    async def test_pool_setting_off_has_no_reason(self):
        with Env(swing(), setting=PoolSetting.create("acc", "US", use_yn="N")) as e:
            self.assertIsNone(await reason(1, 35_200, None, "회수", user_id="u"))
        e.fetch.assert_not_called()


class EquityCacheTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_sells_share_one_balance_call(self):
        """동시에 여러 풀 스윙이 매도돼도 KIS 잔고 조회는 한 번 — 다른 주문의 유량 한도를 쓰지 않는다"""
        with Env(swing(), setting=active("US"), snapshot=AccountSnapshot("US", 1_000_000.0, 1.0)) as e:
            for _ in range(5):
                self.assertEqual(await reason(1, 10_000, None, "회수", user_id="u"), "회수 1.0% (총자산 대비)")
        self.assertEqual(e.fetch.await_count, 1)
        self.assertEqual(e.find_pool.await_count, 1)

    async def test_burst_of_simultaneous_sells_makes_one_balance_call(self):
        """급락으로 풀 스윙이 동시에 매도되면 모두 캐시가 빈 채 들어온다 — 진행 중인 조회를 기다려 잔고 조회는 한 번"""
        import asyncio

        async def slow_fetch(*a, **kw):
            await asyncio.sleep(0.01)
            return AccountSnapshot("US", 1_000_000.0, 1.0)
        with Env(swing(), setting=active("US")) as e:
            e.fetch.side_effect = slow_fetch
            results = await asyncio.gather(*[reason(1, 10_000, None, "회수", user_id="u") for _ in range(8)])
        self.assertEqual(set(results), {"회수 1.0% (총자산 대비)"})
        self.assertEqual(e.fetch.await_count, 1)
        self.assertEqual(SwingOrderExecutor._equity_inflight, {}, "끝나면 진행 중 표시가 남지 않는다")

    async def test_failed_lookup_does_not_leave_the_others_waiting_or_poison_the_cache(self):
        import asyncio

        async def failing_fetch(*a, **kw):
            await asyncio.sleep(0.01)
            raise RuntimeError("KIS 장애")
        with Env(swing(), setting=active("US")) as e:
            e.fetch.side_effect = failing_fetch
            results = await asyncio.gather(*[reason(1, 10_000, None, "회수", user_id="u") for _ in range(4)])
        self.assertEqual(set(results), {None}, "실패는 사유 문구만 생략하고 호출을 막지 않는다")
        self.assertEqual(SwingOrderExecutor._equity_inflight, {})

    async def test_pool_off_is_cached_too(self):
        with Env(swing(), setting=None) as e:
            for _ in range(3):
                self.assertIsNone(await reason(1, 1000, None, "회수", user_id="u"))
        self.assertEqual(e.find_pool.await_count, 1)
        e.fetch.assert_not_called()

    async def test_cache_expires(self):
        import time
        with Env(swing(), setting=active("US"), snapshot=AccountSnapshot("US", 1_000_000.0, 1.0)) as e:
            await reason(1, 10_000, None, "회수", user_id="u")
            key = ("acc", "US")
            ts, eq = SwingOrderExecutor._equity_cache[key]
            SwingOrderExecutor._equity_cache[key] = (ts - SwingOrderExecutor.EQUITY_CACHE_TTL - 1, eq)
            await reason(1, 10_000, None, "회수", user_id="u")
        self.assertEqual(e.fetch.await_count, 2)

    async def test_accounts_and_markets_are_cached_separately(self):
        with Env(swing(mrkt="J"), setting=active("KR"), snapshot=AccountSnapshot("KR", 100_000_000.0, 1.0)) as e:
            await reason(1, 1_000_000, None, "회수", user_id="u")
        with Env(swing(mrkt="NAS"), setting=active("US"), snapshot=AccountSnapshot("US", 1_000_000.0, 1.0)) as e2:
            self.assertEqual(await reason(1, 10_000, None, "회수", user_id="u"), "회수 1.0% (총자산 대비)")


class PoolSellTest(unittest.IsolatedAsyncioTestCase):
    async def test_pool_enabled_reads_current_equity(self):
        with Env(swing(), setting=active("US"), snapshot=AccountSnapshot("US", 1_000_000.0, 900_000.0)) as e:
            self.assertEqual(await reason(1, 26_000, None, "회수", user_id="u"), "회수 2.6% (총자산 대비)")
        e.fetch.assert_awaited_once()
        self.assertEqual(e.fetch.await_args.args[2:], ("acc", "US"))

    async def test_market_mapping_for_domestic_swing(self):
        with Env(swing(mrkt="J"), setting=active("KR"), snapshot=AccountSnapshot("KR", 100_000_000.0, 1.0)) as e:
            self.assertEqual(await reason(1, 1_500_000, None, "회수", user_id="u"), "회수 1.5% (총자산 대비)")
        self.assertEqual(e.find_pool.await_args.args, ("acc", "KR"))

    async def test_pool_off_or_missing_has_no_reason_and_no_kis_call(self):
        for setting in (None, PoolSetting.create("acc", "US", use_yn="N")):
            with Env(swing(), setting=setting) as e:
                self.assertIsNone(await reason(1, 1000, None, "회수", user_id="u"))
            e.fetch.assert_not_called()

    async def test_unreadable_equity_has_no_reason(self):
        with Env(swing(), setting=active(), snapshot=None):
            self.assertIsNone(await reason(1, 1000, None, "회수", user_id="u"))

    async def test_failures_never_propagate(self):
        """KIS·DB 오류가 체결 이력 저장을 막으면 안 된다"""
        with Env(swing(), setting=active(), snapshot_error=RuntimeError("KIS 장애")):
            self.assertIsNone(await reason(1, 1000, None, "회수", user_id="u"))
        with Env(None):
            self.assertIsNone(await reason(1, 1000, None, "회수", user_id="u"))


if __name__ == "__main__":
    unittest.main()
