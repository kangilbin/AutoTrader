"""오더블럭 탐지·백테스트 테스트

실행:
  uv run pytest tests/test_order_block.py -v

규칙: docs/02-design/features/order-block-strategy.design.md (3·4·9장)

기본 시나리오 (ATR 2, 20일 평균거래량 100, EMA120 0 으로 고정):
  봉2  고가 105            → 스윙 고점 (봉4 완성 시 확정)
  봉5  100.5→99 음봉       → 오더블럭 (고가 101 = 근접선, 저가 98.5 = 원격선)
  봉6~7 거래량 200 임펄스   → 봉7 종가 106.5 > 105 : BOS
  봉8  저가 105.5          → 존 미터치
  봉9  저가 100.5          → 첫 리테스트 (매수 101 + 슬리피지 1틱 = 101.01, 손절 98.5 − 0.6 = 97.9, R = 3.11)
"""
import unittest
from unittest.mock import patch
from dataclasses import replace
from typing import List, Tuple

import pandas as pd

from app.domain.swing.backtest.strategies.order_block_backtest_strategy import OrderBlockBacktestStrategy
from app.domain.swing.order_block import (
    OrderBlock, _swept_low, active_order_block, confirmed_pivot_highs, confirmed_pivot_lows,
    detect_order_blocks, weekly_uptrend,
)
from app.domain.swing.trading.strategies.base_order_block import BaseOrderBlockStrategy

# (시가, 고가, 저가, 종가, 거래량)
BASE_BARS: List[Tuple[float, float, float, float, float]] = [
    (100.0, 101.0, 99.0, 100.5, 100),
    (100.5, 102.0, 100.0, 101.5, 100),
    (101.5, 105.0, 101.0, 104.0, 100),    # 2: 스윙 고점
    (104.0, 104.5, 101.0, 102.0, 100),
    (102.0, 103.0, 100.0, 100.5, 100),    # 4: 고점 확정
    (100.5, 101.0, 98.5, 99.0, 100),      # 5: 오더블럭 (음봉)
    (99.0, 103.0, 99.0, 102.5, 200),
    (102.5, 107.0, 102.0, 106.5, 200),    # 7: BOS
    (106.0, 108.0, 105.5, 105.8, 100),    # 8: 미터치
]
RETEST = (105.0, 105.5, 100.5, 101.5, 100)        # 9: 첫 리테스트
AFTER_ENTRY = [
    (102.0, 108.0, 101.5, 107.5, 100),    # 10: 고가 108 ≥ 목표 107.2 → 부분익절
    (107.5, 110.0, 107.0, 109.5, 100),
    (109.0, 111.0, 108.5, 110.0, 100),
    (109.0, 109.5, 103.0, 103.5, 100),    # 13: 고점 111 − ATR×3 = 105 이탈 → 잔량 청산
]


def _df(bars, atr=2.0, avg_vol=100.0, ema=0.0) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=len(bars))
    df = pd.DataFrame(bars, columns=["STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR", "ACML_VOL"])
    df.insert(0, "STCK_BSOP_DATE", dates.strftime("%Y%m%d"))
    df["atr"], df["avg_vol20"], df["ema120"] = atr, avg_vol, ema
    return df


P = BaseOrderBlockStrategy.ob_params()


class PivotTest(unittest.TestCase):

    def test_pivot_needs_right_bars(self):
        """오른쪽 pivot_len 봉이 완성돼야 고점이 확정된다 (선견 방지)"""
        self.assertEqual(confirmed_pivot_highs(pd.Series([1, 2, 5, 3, 4]).to_numpy(), 2), [(2, 4)])
        self.assertEqual(confirmed_pivot_highs(pd.Series([1, 2, 5, 3]).to_numpy(), 2), [])

    def test_equal_high_is_not_pivot(self):
        self.assertEqual(confirmed_pivot_highs(pd.Series([1, 2, 5, 5, 4, 3]).to_numpy(), 2), [])


class DetectTest(unittest.TestCase):

    def test_detects_last_bearish_candle_before_bos(self):
        blocks = detect_order_blocks(_df(BASE_BARS), P)
        self.assertEqual(len(blocks), 1, "봉8도 105를 넘지만 같은 고점의 BOS는 1회뿐")
        ob = blocks[0]
        self.assertEqual((ob.ob_idx, ob.bos_idx), (5, 7))
        self.assertEqual((ob.proximal, ob.distal), (101.0, 98.5))

    def test_proximal_body(self):
        ob = detect_order_blocks(_df(BASE_BARS), replace(P, proximal_source="body"))[0]
        self.assertEqual(ob.proximal, 100.5)   # 음봉 시가 = 몸통 상단

    def test_quality_filters(self):
        weak_volume = [b if i not in (6, 7) else b[:4] + (140,) for i, b in enumerate(BASE_BARS)]
        cases = {
            "거래량 부족 (140 < 100×1.5)": _df(weak_volume),
            "존 폭 초과 (2.5 > 1.6×1.5)": _df(BASE_BARS, atr=1.6),
            "변위 부족 (8 < 4.5×2)": _df(BASE_BARS, atr=4.5),
            "장기 하락 추세 (종가 < EMA120)": _df(BASE_BARS, ema=200.0),
        }
        for name, df in cases.items():
            with self.subTest(name):
                self.assertEqual(detect_order_blocks(df, P), [])


class ActiveTest(unittest.TestCase):

    def setUp(self):
        self.df = _df(BASE_BARS + [RETEST])
        self.blocks = detect_order_blocks(self.df, P)

    def test_active_from_bos_day(self):
        """BOS 당일 완성 후부터 활성 (다음 날 첫 리테스트 가능)"""
        self.assertIsNotNone(active_order_block(self.df, self.blocks, 7, P))
        self.assertIsNone(active_order_block(self.df, self.blocks, 6, P))

    def test_touched_block_is_consumed(self):
        self.assertIsNotNone(active_order_block(self.df, self.blocks, 8, P))
        self.assertIsNone(active_order_block(self.df, self.blocks, 9, P), "봉9 저가가 근접선에 닿음")

    def test_future_bars_do_not_change_result(self):
        """as_of 이후 봉을 지워도 결과가 같다 (선견 회귀 테스트)"""
        truncated = self.df.iloc[:9]
        expected = active_order_block(truncated, detect_order_blocks(truncated, P), 8, P)
        self.assertEqual(active_order_block(self.df, self.blocks, 8, P), expected)

    def test_expires_after_max_age(self):
        short = replace(P, ob_max_age=0)
        self.assertIsNotNone(active_order_block(self.df, self.blocks, 7, short))
        self.assertIsNone(active_order_block(self.df, self.blocks, 8, short))

    def test_most_recent_block_wins(self):
        older = OrderBlock(1, 3, "a", "b", proximal=50.0, distal=49.0)
        newer = OrderBlock(2, 4, "c", "d", proximal=60.0, distal=59.0)
        self.assertEqual(active_order_block(self.df, [older, newer], 8, P), newer)


class _FixedIndicators(OrderBlockBacktestStrategy):
    """지표를 상수로 고정해 가격 경로만으로 시나리오를 만든다"""
    ATR = 2.0

    def _calculate_indicators(self, df):
        df = df.copy()
        df["atr"], df["avg_vol20"], df["ema120"] = self.ATR, 100.0, 0.0
        df["ema20"], df["obv_z"] = df["STCK_CLPR"], 0.0
        return df


# 2026-10-25 이전 청산 값 — 본전 상향·부분익절·단계별 청산선 장치를 검증할 때 명시적으로 쓴다
LEGACY_EXITS = {"STOP_ATR_BUFFER": 0.3, "BREAKEVEN_R": 1.0, "PARTIAL_TP_R": 2.0, "TRAIL_ATR_MULT": 3.0}


class _Legacy(_FixedIndicators):
    STOP_ATR_BUFFER, BREAKEVEN_R, PARTIAL_TP_R, TRAIL_ATR_MULT = 0.3, 1.0, 2.0, 3.0


def _run(bars, strategy=None, **params):
    df = _df(bars).drop(columns=["atr", "avg_vol20", "ema120"])
    p = {"st_code": "X", "mrkt_code": "NAS", "swing_type": "O", "init_amount": 10_000,
         "eval_start": df["STCK_BSOP_DATE"].iloc[0], **params}
    return (strategy or _FixedIndicators()).compute(df, p)


class BacktestScenarioTest(unittest.TestCase):

    def test_full_cycle(self):
        """(이전 청산 값) 첫 리테스트 매수 → 2R 부분익절 → 트레일링 청산"""
        result = _run(BASE_BARS + [RETEST] + AFTER_ENTRY, strategy=_Legacy())
        trades = result["trades"]
        self.assertEqual([t["action"] for t in trades], ["BUY", "SELL", "SELL"])

        buy, partial, final = trades
        self.assertEqual(buy["price"], 101.01)            # min(시가 105, 근접선 101) + 슬리피지 $0.01
        self.assertEqual(buy["quantity"], 64)             # min(10000×2%/3.11 = 64, 10000×80%/101.01 = 79)
        self.assertEqual(partial["price"], 107.22)        # 목표 101.01 + 2 × 3.11 = 107.23 − 슬리피지
        self.assertEqual(partial["quantity"], 32)
        self.assertEqual(final["price"], 104.99)          # 청산선 고점 111 − 2×3 = 105 − 슬리피지
        self.assertIn("이익확정", final["reasons"])

        stats = result["strategy_stats"]
        self.assertEqual(stats["entries"], 1)
        self.assertEqual(stats["days_to_1r"], [0])        # 진입봉 고가 105.5 ≥ 104.1
        self.assertEqual(stats["exit_stages"], {"이익확정": 1})
        avg_exit = (107.22 * 32 + 104.99 * 32) / 64
        self.assertAlmostEqual(stats["avg_r_multiple"], round((avg_exit - 101.01) / 3.11, 2))

    def test_gap_below_distal_invalidates(self):
        gap = (98.0, 100.0, 97.0, 99.5, 100)
        result = _run(BASE_BARS + [gap] + AFTER_ENTRY)
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["strategy_stats"]["gap_invalidations"], 1)

    def test_stop_on_entry_bar(self):
        """진입봉 저가가 손절가에 닿으면 손절로 본다 (장중 순서 불명 → 보수적)"""
        deep = (105.0, 105.5, 96.0, 98.0, 100)                       # 저가 96.0 ≤ 손절 98.5 − 1.0×2 = 96.5
        trades = _run(BASE_BARS + [deep])["trades"]
        self.assertEqual([t["action"] for t in trades], ["BUY", "SELL"])
        self.assertEqual(trades[1]["price"], 96.49)                  # 손절가 96.5 − 슬리피지       # 손절가 97.9 − 슬리피지
        self.assertIn("진입봉", trades[1]["reasons"])

    def test_sharp_retest_still_buys(self):
        """하루에 크게 빠져 존에 닿아도 첫 리테스트면 매수한다 (급락 필터 없음 — 되돌림이 곧 진입 시점)"""
        bars = BASE_BARS[:8] + [(106.0, 108.0, 105.5, 107.0, 100), RETEST]   # 101 / 107 − 1 = −5.6%
        trades = _run(bars)["trades"]
        self.assertEqual(trades[0]["action"], "BUY")
        self.assertEqual(trades[0]["price"], 101.01)
        self.assertTrue(all("최종 청산" in t["reasons"] for t in trades[1:]), "매수 외 매도는 평가 종료 청산뿐")

    def test_stop_too_wide_skips_entry(self):
        class Tight(_FixedIndicators):
            MAX_STOP_LOSS_PCT = 2.0        # 손절폭 3.07% > 2%
        result = _run(BASE_BARS + [RETEST], strategy=Tight())
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["strategy_stats"]["blocked"]["stop_wide"], 1)

    def test_full_entry_mode(self):
        buy = _run(BASE_BARS + [RETEST], full_entry=True)["trades"][0]
        self.assertEqual(buy["quantity"], 98)             # int(10000 × 0.99 / 101.01)

    def test_default_exits_full_cycle(self):
        """기본 청산: 부분익절 없이 보유, 고점 111 − ATR×5 = 101 위라 평가 종료까지 들고 간다"""
        trades = _run(BASE_BARS + [RETEST] + AFTER_ENTRY)["trades"]
        self.assertEqual([t["action"] for t in trades], ["BUY", "SELL"])
        self.assertIn("손절 96.50", trades[0]["reasons"])
        self.assertIn("최종 청산", trades[1]["reasons"])

    def test_registered_in_factory(self):
        from app.domain.swing.backtest.strategy_factory import StrategyFactory
        self.assertIsInstance(StrategyFactory.get_strategy("O"), OrderBlockBacktestStrategy)


class ExitLineTest(unittest.TestCase):
    S = BaseOrderBlockStrategy

    def test_default_has_no_breakeven(self):
        """기본(2026-10-25~): 본전 상향 없음 — +2R 전까지 손절가 유지, 이후 고점 − ATR×5"""
        entry, stop, atr = 100.0, 97.0, 2.0                       # R = 3
        self.assertEqual(self.S.calculate_exit_line(entry, 105.9, stop, atr), 97.0)    # +1R 넘어도 손절가
        self.assertEqual(self.S.calculate_exit_line(entry, 106.0, stop, atr), 97.0)    # +2R, 106−10=96 < 97
        self.assertEqual(self.S.calculate_exit_line(entry, 112.0, stop, atr), 102.0)   # 112−10
        self.assertEqual(self.S.exit_stage(entry, 104.0, stop), "손절", "본전 단계가 꺼져 있다")
        self.assertIsNone(self.S.partial_target(entry, stop), "부분익절이 꺼져 있다")

    def test_stages_with_legacy_values(self):
        with patch.multiple(BaseOrderBlockStrategy, **LEGACY_EXITS):
            entry, stop, atr = 100.0, 97.0, 2.0
            self.assertEqual(self.S.calculate_exit_line(entry, 102.9, stop, atr), 97.0)    # < +1R
            self.assertEqual(self.S.calculate_exit_line(entry, 103.0, stop, atr), 100.0)   # +1R → 본전
            self.assertEqual(self.S.calculate_exit_line(entry, 106.0, stop, atr), 100.0)   # +2R, 106−6=100
            self.assertEqual(self.S.calculate_exit_line(entry, 110.0, stop, atr), 104.0)   # 110−6
            self.assertEqual(self.S.exit_stage(entry, 104.0, stop), "본전방어")
            self.assertEqual(self.S.partial_target(entry, stop), 106.0)

    def test_non_positive_r_exits_at_stop(self):
        """체결가가 손절가 이하면 청산선 = 손절가 (0이면 실전에서 영영 안 판다)"""
        self.assertEqual(self.S.calculate_exit_line(97.0, 97.0, 98.0, 2.0), 98.0)



# ==================== v2 조건 (백테스트 전용, 기본 꺼짐) ====================

# 고점 103 → BOS 종가 103.2 → 상승폭이 작아 근접선 101이 임펄스 50%(100.9) 위 = 할인 구간 아님
SHALLOW_BARS = [
    (100.0, 101.0, 99.0, 100.5, 100),
    (100.5, 102.0, 100.0, 101.5, 100),
    (101.5, 103.0, 101.0, 102.0, 100),    # 2: 고점 103
    (102.0, 102.5, 101.0, 101.5, 100),
    (101.5, 102.0, 100.0, 100.5, 100),
    (100.5, 101.0, 98.5, 99.0, 100),      # 5: OB
    (99.0, 102.5, 99.0, 102.3, 200),
    (102.3, 103.3, 102.0, 103.2, 200),    # 7: BOS (103.2 > 103)
]


class QualityConditionTest(unittest.TestCase):

    def test_fvg_required(self):
        self.assertEqual(len(detect_order_blocks(_df(BASE_BARS), replace(P, require_fvg=True))), 1,
                         "봉7 저가 102 > 봉5 고가 101 — 갭이 있다")
        no_gap = [b if i != 7 else (102.5, 107.0, 100.5, 106.5, 200) for i, b in enumerate(BASE_BARS)]
        self.assertEqual(detect_order_blocks(_df(no_gap), replace(P, require_fvg=True)), [])

    def test_discount_required(self):
        self.assertEqual(len(detect_order_blocks(_df(SHALLOW_BARS), P)), 1)
        self.assertEqual(detect_order_blocks(_df(SHALLOW_BARS), replace(P, require_discount=True)), [])
        self.assertEqual(len(detect_order_blocks(_df(BASE_BARS), replace(P, require_discount=True))), 1)

    def test_sweep(self):
        lows = pd.Series([10, 9, 8, 9, 10, 11, 12, 11, 7.5, 9]).to_numpy(dtype=float)   # 저점 8(봉2) → 봉8이 깸
        pivots = confirmed_pivot_lows(lows, 2)
        self.assertTrue(_swept_low(lows, 8, pivots))
        self.assertFalse(_swept_low(lows, 6, pivots), "직전 저점을 깨지 않았다")

    def test_proximal_mean(self):
        ob = detect_order_blocks(_df(BASE_BARS), replace(P, proximal_source="mean"))[0]
        self.assertEqual(ob.proximal, (101.0 + 98.5) / 2)


class WeeklyStructureTest(unittest.TestCase):

    def test_no_lookahead(self):
        """행 i의 판정은 지난주까지 — 이후 봉을 지워도 앞부분 판정이 같다"""
        import math
        # 상승 추세 + 30일 주기 눌림 (주봉 스윙이 생기도록)
        closes = [100 + x * 0.4 + 6 * math.sin(x / 30 * 2 * math.pi) for x in range(260)]
        bars = [(c, c + 1, c - 1, c, 100) for c in closes]
        full = weekly_uptrend(_df(bars))
        part = weekly_uptrend(_df(bars[:150]))
        self.assertTrue((full[:145] == part[:145]).all(), "이후 봉이 앞부분 판정을 바꿨다")
        self.assertTrue(full[-40:].all(), "꾸준한 상승 추세인데 상승 구조로 판정되지 않음")

    def test_breakdown_clears_uptrend(self):
        import math
        up = [100 + x * 0.4 + 6 * math.sin(x / 30 * 2 * math.pi) for x in range(200)]
        down = [up[-1] - x * 1.0 for x in range(1, 80)]                # 급락으로 직전 스윙 저점 이탈
        bars = [(c, c + 1, c - 1, c, 100) for c in up + down]
        flags = weekly_uptrend(_df(bars))
        self.assertTrue(flags[190])
        self.assertFalse(flags[-1], "직전 스윙 저점을 깨고 내려갔는데 상승 구조로 남음")


class _V2(_FixedIndicators):
    pass


class EntryExitConditionTest(unittest.TestCase):

    def test_market_filter_fail_closed(self):
        strategy = type("M", (_V2,), {"MARKET_FILTER": True})()
        result = _run(BASE_BARS + [RETEST], strategy=strategy)
        self.assertEqual(result["trades"], [], "지수 국면 데이터가 없으면 사지 않는다")
        self.assertEqual(result["strategy_stats"]["blocked"]["market"], 1)

        dates = pd.to_datetime(_df(BASE_BARS + [RETEST])["STCK_BSOP_DATE"])
        bull = pd.Series(True, index=dates)
        result = _run(BASE_BARS + [RETEST], strategy=type("M", (_V2,), {"MARKET_FILTER": True})(),
                      market_regime=bull)
        self.assertEqual(result["trades"][0]["action"], "BUY")

    def test_rebound_entry_price(self):
        """저가 100.5 + 0.3×ATR(2) = 101.1 까지 반등했을 때 그 가격(+슬리피지)에 산다"""
        strategy = type("R", (_V2,), {"REBOUND_ATR": 0.3})()
        buy = _run(BASE_BARS + [RETEST], strategy=strategy)["trades"][0]
        self.assertEqual(buy["price"], 101.11)

    def test_rebound_after_intraday_break_is_skipped(self):
        """저가가 원격선(98.5) 아래면 장중 붕괴 — 반등해도 사지 않는다 (실전 ob_broken과 같은 규칙)"""
        broken = (105.0, 105.5, 98.0, 101.5, 100)
        strategy = type("R", (_V2,), {"REBOUND_ATR": 0.3})()
        result = _run(BASE_BARS + [broken], strategy=strategy)
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["strategy_stats"]["blocked"]["rebound"], 1)

    def test_rebound_too_far_is_not_chased(self):
        strategy = type("R", (_V2,), {"REBOUND_ATR": 0.8})()        # 100.5 + 1.6 = 102.1 > 근접선 + 0.5
        result = _run(BASE_BARS + [RETEST], strategy=strategy)
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["strategy_stats"]["blocked"]["rebound"], 1)

    def test_time_stop(self):
        weak_retest = (103.0, 103.5, 100.5, 101.5, 100)               # 고가 103.5 < +1R 104.12
        flat = [(101.5, 102.0, 100.8, 101.6, 100), (101.6, 102.2, 101.0, 101.8, 100),
                (101.8, 102.0, 101.2, 101.5, 100)]
        strategy = type("T", (_V2,), {"TIME_STOP_DAYS": 2})()
        trades = _run(BASE_BARS + [weak_retest] + flat, strategy=strategy)["trades"]
        self.assertEqual([t["action"] for t in trades], ["BUY", "SELL"])
        self.assertIn("시간청산", trades[1]["reasons"])
        self.assertEqual(trades[1]["price"], 101.59)                 # 진입 2거래일 뒤 시가 101.6 − 슬리피지

    def test_swing_target(self):
        """부분익절 목표 = BOS 이후 전일까지 최고가 108 (1.5R~3R 범위 안)"""
        strategy = type("S", (_V2,), {"TARGET_MODE": "swing"})()
        trades = _run(BASE_BARS + [RETEST] + AFTER_ENTRY, strategy=strategy)["trades"]
        self.assertEqual(trades[1]["price"], 107.99)                 # max(108, 시가 102) − 슬리피지

if __name__ == "__main__":
    unittest.main()
