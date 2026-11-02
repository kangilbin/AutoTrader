"""
리스크 기반 포지션 사이징 테스트

실행:
  uv run pytest tests/test_risk_sizing.py -v

백테스트 엔진과 실전 매수 경로가 같은 함수를 쓰므로, 수량·한도 판정의 경계 조건을 여기서 고정한다.
"""
import unittest

from app.domain.swing.risk_sizing import (
    BINDING_POSITION_CAP,
    BINDING_RISK,
    REJECT_CASH,
    REJECT_MAX_POSITIONS,
    REJECT_OPEN_RISK,
    calc_qty,
    check_limits,
    position_open_risk,
    total_open_risk,
    within_liquidity,
)

EQUITY = 1_000_000_000  # 10억 → 1% = 1,000만 원


class CalcQtyTest(unittest.TestCase):
    def test_risk_one_percent(self):
        """손절폭 1,000원이면 1,000만 원 ÷ 1,000 = 10,000주"""
        r = calc_qty(EQUITY, 0.01, entry_price=50_000, stop_price=49_000)
        self.assertEqual(r.qty, 10_000)
        self.assertEqual(r.risk_amount, 10_000_000)
        self.assertEqual(r.binding, BINDING_RISK)

    def test_qty_floors(self):
        """나머지는 버린다 — 리스크가 목표를 넘지 않아야 한다"""
        r = calc_qty(EQUITY, 0.01, entry_price=50_000, stop_price=48_700)  # 손절폭 1,300
        self.assertEqual(r.qty, 7_692)
        self.assertLessEqual(r.risk_amount, 10_000_000)

    def test_position_cap_binds(self):
        """손절폭이 좁으면 수량이 과해진다 → 종목당 상한이 먼저 걸린다"""
        # 손절폭 2% → 리스크 수량 = 5억어치. 상한 20%(2억)가 걸려야 한다
        r = calc_qty(EQUITY, 0.01, entry_price=50_000, stop_price=49_000, max_position_pct=None)
        self.assertEqual(r.qty * 50_000, 500_000_000)
        capped = calc_qty(EQUITY, 0.01, entry_price=50_000, stop_price=49_000, max_position_pct=0.2)
        self.assertEqual(capped.qty, 4_000)
        self.assertEqual(capped.binding, BINDING_POSITION_CAP)
        self.assertEqual(capped.risk_amount, 4_000_000)  # 상한에 걸리면 실제 리스크는 1%보다 작다

    def test_cap_not_binding_when_risk_smaller(self):
        """손절폭이 넓으면 리스크 수량이 이미 상한 아래"""
        r = calc_qty(EQUITY, 0.01, entry_price=100_000, stop_price=93_000, max_position_pct=0.2)  # 손절 7%
        self.assertEqual(r.qty, 1_428)
        self.assertEqual(r.binding, BINDING_RISK)

    def test_invalid_stop_gives_zero(self):
        """청산선이 진입가 이상이면 리스크를 계산할 수 없다 → 사지 않는다"""
        for stop in (50_000, 51_000):
            r = calc_qty(EQUITY, 0.01, entry_price=50_000, stop_price=stop)
            self.assertEqual(r.qty, 0)
            self.assertEqual(r.risk_amount, 0.0)

    def test_invalid_inputs_give_zero(self):
        self.assertEqual(calc_qty(0, 0.01, 50_000, 49_000).qty, 0)
        self.assertEqual(calc_qty(EQUITY, 0, 50_000, 49_000).qty, 0)
        self.assertEqual(calc_qty(EQUITY, 0.01, 0, 49_000).qty, 0)

    def test_small_equity_rounds_to_zero(self):
        """총자산이 작아 1주도 못 사면 0 (소수점 매매 없음)"""
        r = calc_qty(1_000_000, 0.01, entry_price=500_000, stop_price=465_000)  # 1% = 1만 원, 손절폭 35,000
        self.assertEqual(r.qty, 0)

    def test_us_decimal_prices(self):
        """미국 종목 — 소수점 가격"""
        r = calc_qty(100_000, 0.01, entry_price=150.25, stop_price=140.0)  # 1% = $1,000, 손절폭 10.25
        self.assertEqual(r.qty, 97)


class OpenRiskTest(unittest.TestCase):
    def test_position_open_risk(self):
        self.assertEqual(position_open_risk(100, 93, 10), 70)

    def test_zero_when_line_at_or_above_entry(self):
        """청산선이 본전 이상이면 열린 리스크 0 — 이익 포지션이 한도를 차지하지 않는다"""
        self.assertEqual(position_open_risk(100, 100, 10), 0)
        self.assertEqual(position_open_risk(100, 120, 10), 0)

    def test_zero_qty(self):
        self.assertEqual(position_open_risk(100, 93, 0), 0)

    def test_total(self):
        self.assertEqual(total_open_risk([(100, 93, 10), (50, 50, 100), (200, 190, 5)]), 70 + 0 + 50)

    def test_total_empty(self):
        self.assertEqual(total_open_risk([]), 0)


class CheckLimitsTest(unittest.TestCase):
    base = dict(order_amount=100, new_risk=10, cash=1_000, equity=10_000,
                position_count=0, open_risk=0)

    def check(self, **overrides):
        return check_limits(**{**self.base, **overrides})

    def test_no_limits_passes(self):
        self.assertIsNone(self.check())

    def test_cash_shortage(self):
        self.assertEqual(self.check(order_amount=1_001), REJECT_CASH)

    def test_max_positions(self):
        self.assertIsNone(self.check(position_count=9, max_positions=10))
        self.assertEqual(self.check(position_count=10, max_positions=10), REJECT_MAX_POSITIONS)

    def test_open_risk_limit_boundary(self):
        """총 열린 리스크 8% = 800. 정확히 한도면 통과, 넘으면 거절"""
        self.assertIsNone(self.check(open_risk=790, new_risk=10, max_open_risk_pct=0.08))
        self.assertEqual(self.check(open_risk=791, new_risk=10, max_open_risk_pct=0.08), REJECT_OPEN_RISK)

    def test_reject_order_cash_first(self):
        """여러 한도가 동시에 걸리면 현금 → 종목 수 → 총 리스크 순으로 사유를 남긴다"""
        r = self.check(order_amount=2_000, position_count=10, max_positions=10,
                       open_risk=900, max_open_risk_pct=0.08)
        self.assertEqual(r, REJECT_CASH)
        r = self.check(position_count=10, max_positions=10, open_risk=900, max_open_risk_pct=0.08)
        self.assertEqual(r, REJECT_MAX_POSITIONS)


class LiquidityTest(unittest.TestCase):
    def test_within(self):
        self.assertTrue(within_liquidity(500, 10_000, 0.05))   # 정확히 5%
        self.assertFalse(within_liquidity(501, 10_000, 0.05))

    def test_unknown_adv_blocks(self):
        """일평균 거래대금을 모르면 막는다 (실전 기존 동작과 같다)"""
        self.assertFalse(within_liquidity(1, 0, 0.05))


if __name__ == "__main__":
    unittest.main()
