"""
연속 손실 가드 — 같은 종목 전량 청산이 연속 2번 손실이면 60일간 신규 진입을 막는다

실행: uv run pytest tests/test_loss_guard.py -v

구성
  1) 손실 판정·상태 전이(순수 함수)
  2) Redis 상태 기록·조회 (깨진 값·만료·차단 중 청산)
  3) 실제 배치 사이클: 한 번 손실은 통과(SNOW 형), 연속 2손실은 차단, 청산 시 결과 기록
"""
import json
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from app.domain.swing.trading import auto_swing_batch as batch
from app.domain.swing.trading import close_confirm
from app.domain.swing.trading import loss_guard as lg
from tests.test_swing_trade_scenarios import FakeRedis, Market, SwingScenarioBase

TODAY = date(2026, 11, 12)


class PureTest(unittest.TestCase):
    def test_is_loss(self):
        self.assertTrue(lg.is_loss(100, 99.9))
        self.assertFalse(lg.is_loss(100, 100))
        self.assertFalse(lg.is_loss(100, 120))

    def test_is_loss_unknown_when_price_missing(self):
        for entry, exit_ in [(None, 90), (100, None), (0, 90), (100, 0), ("x", 90)]:
            self.assertIsNone(lg.is_loss(entry, exit_), (entry, exit_))

    def test_first_loss_counts_without_ban(self):
        self.assertEqual(lg.next_state(0, True, TODAY), {"losses": 1, "ban_until": None})

    def test_second_consecutive_loss_bans_and_resets_count(self):
        state = lg.next_state(1, True, TODAY)
        self.assertEqual(state, {"losses": 0, "ban_until": (TODAY + timedelta(days=60)).isoformat()})

    def test_win_resets_streak(self):
        self.assertEqual(lg.next_state(1, False, TODAY), {"losses": 0, "ban_until": None})

    def test_unknown_result_keeps_streak(self):
        self.assertEqual(lg.next_state(1, None, TODAY), {"losses": 1, "ban_until": None})


class RedisStateTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.redis = FakeRedis()

    async def exit_(self, entry, exit_, today=TODAY):
        await lg.record_exit(self.redis, 7, entry, exit_, today)

    async def test_two_losses_ban_for_60_days_then_expire(self):
        await self.exit_(100, 90)
        self.assertIsNone(await lg.banned_until(self.redis, 7, TODAY))
        await self.exit_(100, 95)
        until = TODAY + timedelta(days=60)
        self.assertEqual(await lg.banned_until(self.redis, 7, TODAY), until)
        self.assertEqual(await lg.banned_until(self.redis, 7, until), until, "만료일 당일까지는 차단")
        self.assertIsNone(await lg.banned_until(self.redis, 7, until + timedelta(days=1)))

    async def test_win_between_losses_prevents_ban(self):
        await self.exit_(100, 90)
        await self.exit_(100, 110)
        await self.exit_(100, 90)
        self.assertIsNone(await lg.banned_until(self.redis, 7, TODAY))

    async def test_streak_is_per_swing(self):
        await self.exit_(100, 90)
        await lg.record_exit(self.redis, 8, 100, 90, TODAY)
        self.assertIsNone(await lg.banned_until(self.redis, 7, TODAY))
        self.assertIsNone(await lg.banned_until(self.redis, 8, TODAY))

    async def test_exit_during_ban_keeps_expiry(self):
        await self.exit_(100, 90)
        await self.exit_(100, 91)
        until = await lg.banned_until(self.redis, 7, TODAY)
        self.assertIsNotNone(until)
        await self.exit_(100, 120, TODAY + timedelta(days=5))     # 차단 중 편입 포지션이 이익 청산
        self.assertEqual(await lg.banned_until(self.redis, 7, TODAY), until)

    async def test_ban_restarts_counting_after_trigger(self):
        await self.exit_(100, 90)
        await self.exit_(100, 91)                                  # 차단 + 횟수 0
        later = TODAY + timedelta(days=70)
        await self.exit_(100, 90, later)                           # 만료 뒤 첫 손실 — 아직 차단 아님
        self.assertIsNone(await lg.banned_until(self.redis, 7, later))

    async def test_same_exit_recorded_twice_counts_once(self):
        """Redis 기록 뒤 DB 커밋이 실패해 같은 청산이 다시 들어와도 한 번만 센다 (한 번 손실로 60일 차단되면 안 된다)"""
        await self.exit_(100, 90)
        await self.exit_(100, 90)
        self.assertIsNone(await lg.banned_until(self.redis, 7, TODAY))
        self.assertEqual(json.loads(self.redis.store[lg.state_key(7)])["losses"], 1)
        await self.exit_(100, 90, TODAY + timedelta(days=3))       # 다른 날의 같은 가격은 별개의 청산
        self.assertIsNotNone(await lg.banned_until(self.redis, 7, TODAY))

    async def test_corrupt_or_missing_state_does_not_ban(self):
        self.assertIsNone(await lg.banned_until(self.redis, 7, TODAY))
        for raw in ("not-json", "[]", json.dumps({"ban_until": "garbage"}), b"\xff"):
            self.redis.store[lg.state_key(7)] = raw
            self.assertIsNone(await lg.banned_until(self.redis, 7, TODAY), raw)

    async def test_record_full_exit_passes_prices_and_keeps_last_exit(self):
        with patch.object(close_confirm, "trading_date", return_value=TODAY):
            await close_confirm.record_full_exit(self.redis, 7, "J", entry_price=100, exit_price=90)
            await close_confirm.record_full_exit(self.redis, 7, "J", entry_price=100, exit_price=91)
            self.assertEqual(await close_confirm.get_last_exit(self.redis, 7), TODAY)
        self.assertEqual(await lg.banned_until(self.redis, 7, TODAY), TODAY + timedelta(days=60))

    async def test_record_full_exit_without_prices_leaves_streak(self):
        with patch.object(close_confirm, "trading_date", return_value=TODAY):
            await close_confirm.record_full_exit(self.redis, 7, "J", entry_price=100, exit_price=90)
            await close_confirm.record_full_exit(self.redis, 7, "J")
            await close_confirm.record_full_exit(self.redis, 7, "J", entry_price=100, exit_price=91)
        self.assertEqual(await lg.banned_until(self.redis, 7, TODAY), TODAY + timedelta(days=60))


class BatchGuardScenarioTest(SwingScenarioBase):
    """실제 배치 사이클: 차단은 신규 진입만 막고 신호 상태·청산은 건드리지 않는다"""

    KEY = lg.state_key(1)

    def put_state(self, losses=0, ban_until=None):
        self.redis.store[self.KEY] = json.dumps({"losses": losses, "ban_until": ban_until})

    def ban(self, days_left=30):
        self.put_state(0, (date.today() + timedelta(days=days_left)).isoformat())

    async def test_banned_blocks_entry_and_logs_reason(self):
        self.ban()
        with self.assertLogs(batch.logger, level="INFO") as logs:
            await self.run_cycles(4)
        self.assertEqual(len(Market.buys()), 0, "차단 중인데 매수했다")
        self.assertEqual(self.swing.SIGNAL, 0)
        self.assertTrue(any("연속 손실 가드" in m for m in logs.output))

    async def test_single_loss_does_not_block(self):
        """SNOW 형: 손실 한 번만으로는 막지 않는다 — 두 번째 진입 신호는 통과"""
        self.put_state(1, None)
        await self.enter_position()
        self.assertEqual(len(Market.buys()), 1)

    async def test_expired_ban_does_not_block(self):
        self.put_state(0, (date.today() - timedelta(days=1)).isoformat())
        await self.enter_position()
        self.assertEqual(len(Market.buys()), 1)

    async def test_disabled_flag_does_not_block(self):
        self.ban()
        with patch.object(lg, "enabled", return_value=False):
            await self.enter_position()
        self.assertEqual(len(Market.buys()), 1)

    async def test_corrupt_state_does_not_block(self):
        self.redis.store[self.KEY] = "garbage"
        await self.enter_position()
        self.assertEqual(len(Market.buys()), 1)

    async def test_redis_failure_does_not_block(self):
        async def boom(*a, **k):
            raise RuntimeError("redis down")
        with patch.object(lg, "banned_until", boom):
            await self.enter_position()
        self.assertEqual(len(Market.buys()), 1)

    async def test_ban_blocks_even_when_credit_gate_allows(self):
        """우선순위: 가드 > Q3 — 스프레드가 허용이어도 가드가 막는다"""
        from app.domain.swing.trading import credit_gate as cg
        end = date.today() - timedelta(days=1)
        vals = (3.40, 3.32, 3.48, 3.42, 3.34, 3.30)
        obs = [(end - timedelta(days=len(vals) - 1 - i), v) for i, v in enumerate(vals)]
        await cg.store_observations(self.redis, obs)
        self.ban()
        await self.run_cycles(3)
        self.assertEqual(len(Market.buys()), 0)

    async def test_ban_does_not_block_exit(self):
        await self.enter_position()
        self.ban()
        Market.move(80.0)
        await self.run_cycle()
        self.assertGreaterEqual(len(Market.sells()), 1, "차단 상태에서 청산이 막혔다")

    async def test_loss_exit_is_recorded_in_streak(self):
        """청산 경로가 결과를 남긴다: 손실 청산 → 연속 손실 1"""
        await self.enter_position()
        Market.move(80.0)
        await self.run_cycle()
        self.assertGreaterEqual(len(Market.sells()), 1)
        state = json.loads(self.redis.store[self.KEY])
        self.assertEqual(state["losses"], 1)


if __name__ == "__main__":
    unittest.main()
