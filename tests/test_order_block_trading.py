"""오더블럭 실전 매매 시나리오 테스트 — 실제 배치(process_single_swing) + 실제 전략

실행:
  uv run pytest tests/test_order_block_trading.py -v

test_swing_trade_scenarios 의 가짜 DB/Redis/KIS 를 그대로 쓰고, 스윙만 SWING_TYPE 'O'로 바꾼다.

기본 상황 (미국 종목, ATR 2, 기본 청산: 버퍼 1.0·본전 없음·부분익절 없음·트레일 ×5):
  활성 OB: 근접선 101.0, 원격선 98.5  → 손절가 = 98.5 − 1.0×2 = 96.5
  현재가 101, 당일 저가 99.5         → 존 터치 + 존 안 → 2사이클 연속이면 매수
  체결가 = 매도호가 101.02           → R = 101.02 − 96.5 = 4.52
본전 상향·부분익절 장치는 이전 값(LEGACY_EXITS)을 명시해 검증한다.
"""
import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from app.domain.swing.trading import auto_swing_batch as batch
from app.domain.swing.trading.strategies.order_block_strategy import OrderBlockStrategy
from app.domain.swing.trading.trading_strategy_factory import TradingStrategyFactory
from tests.test_swing_trade_scenarios import FakeRedis, Market, Row, SwingScenarioBase

LEGACY_EXITS = {"STOP_ATR_BUFFER": 0.3, "BREAKEVEN_R": 1.0, "PARTIAL_TP_R": 2.0, "TRAIL_ATR_MULT": 3.0}

ZONE = {"ob_date": "20260801", "bos_date": "20260805", "proximal": 101.0, "distal": 98.5,
        "as_of_date": "20260813"}


class OBRedis(FakeRedis):
    async def exists(self, key):
        return 1 if key in self.store else 0


class OBRow(Row):
    SWING_TYPE = "O"


class OrderBlockTradingTest(SwingScenarioBase):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.swing.SWING_TYPE = "O"
        store = self.redis.store
        self.redis = OBRedis()
        self.redis.store.update(store)
        self.redis.store["ob_zone:AAPL"] = json.dumps(ZONE)

    async def run_cycles(self, n=1):
        for _ in range(n):
            await batch.process_single_swing(OBRow(), self.redis)

    async def enter(self):
        await self.run_cycles(OrderBlockStrategy.CONSECUTIVE_REQUIRED)
        self.assertEqual(self.swing.SIGNAL, 1, "오더블럭 매수가 체결되지 않음")

    # ==================== 매수 ====================

    async def test_factory_returns_order_block(self):
        self.assertIs(TradingStrategyFactory.get_strategy("O"), OrderBlockStrategy)

    async def test_buy_on_first_retest_with_r_sizing(self):
        await self.run_cycles(1)
        self.assertEqual(Market.buys(), [], "연속 확인 전 매수")
        await self.run_cycles(1)

        self.assertEqual(len(Market.buys()), 1)
        self.assertEqual(self.swing.STOP_PRICE, Decimal("96.50"), "손절가가 매수 시 확정되지 않음")
        # min(100,000 × 2% / (101 − 96.5) = 444, 100,000 × 80% / 101 = 792)
        self.assertEqual(Market.buys()[0]["qty"], 444)
        self.assertEqual(self.redis.store.get("ob_used:1:20260814"), "20260801", "같은 날 재진입 방지 키 없음")

    async def test_no_zone_no_buy(self):
        del self.redis.store["ob_zone:AAPL"]
        await self.run_cycles(3)
        self.assertEqual(Market.buys(), [])

    async def test_price_far_above_zone_is_not_chased(self):
        Market.move(103.0)                       # 근접선 + 0.25×ATR = 101.5 초과
        await self.run_cycles(3)
        self.assertEqual(Market.buys(), [])

    async def test_intraday_break_invalidates_day(self):
        """장중 한 번이라도 원격선 아래였으면 반등해도 그날은 사지 않는다"""
        Market.move(98.0)
        await self.run_cycles(1)
        Market.move(101.0)
        await self.run_cycles(3)
        self.assertEqual(Market.buys(), [])

    async def test_no_reentry_same_day_after_stop(self):
        await self.enter()
        Market.move(96.0)                        # 손절 (96.5 이탈)
        await self.run_cycles(1)
        self.assertEqual(self.swing.SIGNAL, 3)
        self.swing.SIGNAL = 0                    # 쿨다운이 장중에 다 지나간 상황
        self.redis.store.pop("ob_broken:AAPL:20260814", None)
        Market.move(101.0)
        await self.run_cycles(3)
        self.assertEqual(len(Market.buys()), 1, "같은 날 같은 OB로 재매수")

    # ==================== 매도 ====================

    async def test_stop_loss_at_stop_price(self):
        await self.enter()
        Market.move(97.0)                        # 원격선 아래지만 손절가(96.5) 위 → 보유
        await self.run_cycles(1)
        self.assertEqual(Market.sells(), [])
        Market.move(96.4)
        await self.run_cycles(1)
        self.assertEqual(len(Market.sells()), 1)
        self.assertEqual(self.swing.SIGNAL, 3)
        self.assertIsNone(self.swing.STOP_PRICE, "포지션 종료 후 손절가가 남음")

    async def test_default_holds_through_pullback(self):
        """기본 청산: +1R 뒤 진입가로 돌아와도 팔지 않는다 (본전 상향 없음), +2R에서도 부분익절 없음"""
        await self.enter()                       # 손절가 96.5, R = 4.52
        Market.move(106.0)                       # +1R(105.54) 돌파
        await self.run_cycles(1)
        Market.move(100.0)                       # 진입가 아래로 되돌림 — 이전 값이면 본전 이탈로 팔았다
        await self.run_cycles(1)
        self.assertEqual(Market.sells(), [], "본전 상향이 꺼져 있어야 한다")

        Market.move(111.0)                       # +2R(110.06) 돌파 — 부분익절 없음
        await self.run_cycles(1)
        self.assertEqual(self.swing.SIGNAL, 1, "부분익절이 꺼져 있어야 한다")
        Market.move(101.0)                       # 청산선 = 111 − 2×5 = 101 → 이탈
        await self.run_cycles(1)
        self.assertEqual(len(Market.sells()), 1)
        self.assertEqual(self.swing.SIGNAL, 3)

    async def test_breakeven_after_one_r(self):
        with patch.multiple(OrderBlockStrategy, **LEGACY_EXITS):
            await self.enter()                   # 손절가 97.9, R = 3.12
            Market.move(104.5)                   # 고점 ≥ 101.02 + 3.12 → 청산선 본전
            await self.run_cycles(1)
            Market.move(101.0)                   # 본전(101.02) 이탈
            await self.run_cycles(1)
        self.assertEqual(len(Market.sells()), 1)
        self.assertEqual(self.swing.SIGNAL, 3)

    async def test_partial_at_two_r_keeps_stop_and_peak(self):
        with patch.multiple(OrderBlockStrategy, **LEGACY_EXITS):
            await self.enter()
            Market.move(107.3)                   # 목표 101.02 + 2×3.12 = 107.26
            await self.run_cycles(1)
        self.assertEqual(self.swing.SIGNAL, 2, "2R 부분익절이 안 됨")
        self.assertEqual(len(Market.sells()), 1)
        self.assertEqual(self.swing.STOP_PRICE, Decimal("97.90"), "부분익절 후 손절가 유실")   # 이전 버퍼 0.3
        self.assertGreaterEqual(float(self.swing.PEAK_PRICE), 107.3, "부분익절 후 PEAK 리셋됨")

    async def test_missing_stop_falls_back(self):
        """편입 포지션처럼 STOP_PRICE가 없어도 −7%에서 손절된다"""
        await self.enter()
        self.swing.STOP_PRICE = None
        Market.move(95.0)                        # 101.02 × 0.93 = 93.95 위 → 보유
        await self.run_cycles(1)
        self.assertEqual(Market.sells(), [])
        Market.move(93.5)
        await self.run_cycles(1)
        self.assertEqual(len(Market.sells()), 1)


    async def test_unconfirmed_buy_carries_stop_price(self):
        """체결 확인이 다음 사이클로 밀려도 손절가가 빠지지 않는다 (지연 체결은 transition_to_buy를 거치지 않는다)"""
        Market.fill_ratio = 0.0                  # 주문은 나갔지만 체결 확인 실패
        await self.run_cycles(OrderBlockStrategy.CONSECUTIVE_REQUIRED)
        pending = json.loads(self.redis.store["pending_order:1"])
        self.assertEqual(pending["stop_price"], 96.5)

class OrderBlockDescriptionTest(unittest.TestCase):

    def test_numbers_follow_constants(self):
        with patch.object(OrderBlockStrategy, "STOP_ATR_BUFFER", 0.5), \
                patch.object(OrderBlockStrategy, "PARTIAL_TP_R", 3.0):
            desc = OrderBlockStrategy.describe()
        self.assertIn("ATR×0.5", desc["청산"][0])
        self.assertIn("+3R", desc["청산"][3])



if __name__ == "__main__":
    unittest.main()
