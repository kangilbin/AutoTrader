"""오더블럭 v3 연구 도구 — 스윙 합산 곡선·지표 계산 검증 (scripts/research/swing_portfolio.py)

실행:
  uv run pytest tests/test_research_portfolio.py -v
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))

import swing_portfolio as sp  # noqa: E402


def _prices(closes):
    dates = pd.bdate_range("2021-01-04", periods=len(closes))
    return pd.DataFrame({"STCK_BSOP_DATE": dates.strftime("%Y%m%d"), "STCK_CLPR": closes})


class EquityCurveTest(unittest.TestCase):

    def test_buy_hold_sell_domestic(self):
        df = _prices([100, 100, 110, 120, 120])
        d = pd.bdate_range("2021-01-04", periods=5)
        trades = [
            {"date": d[1], "action": "BUY", "quantity": 10, "amount": 1000.0, "commission": 1.0},
            {"date": d[3], "action": "SELL", "quantity": 10, "amount": 1200.0, "net_proceeds": 1195.0},
        ]
        curve = sp.equity_curve(df, trades, "2021-01-04", "J")
        alloc = sp.ALLOCATION
        self.assertEqual(curve["equity"].iloc[0], alloc)                       # 매수 전
        self.assertEqual(curve["equity"].iloc[1], alloc - 1001 + 1000)         # 매수일: 현금 − 비용 + 평가
        self.assertEqual(curve["equity"].iloc[2], alloc - 1001 + 1100)
        self.assertEqual(curve["equity"].iloc[4], alloc - 1001 + 1195)         # 매도 후 현금만
        self.assertEqual(curve["invested"].iloc[4], 0)

    def test_us_pays_fx_both_ways(self):
        df = _prices([100, 100, 100])
        d = pd.bdate_range("2021-01-04", periods=3)
        trades = [
            {"date": d[0], "action": "BUY", "quantity": 10, "amount": 1000.0, "commission": 0.0},
            {"date": d[1], "action": "SELL", "quantity": 10, "amount": 1000.0, "net_proceeds": 1000.0},
        ]
        curve = sp.equity_curve(df, trades, "2021-01-04", "NAS")
        self.assertAlmostEqual(curve["equity"].iloc[-1], sp.ALLOCATION - 2 * 1000 * sp.FX_COST)

    def test_starts_at_period_start(self):
        curve = sp.equity_curve(_prices([1, 2, 3, 4]), [], "2021-01-06", "J")
        self.assertEqual(len(curve), 2)


class PortfolioMetricsTest(unittest.TestCase):

    def test_drawdown_and_streak(self):
        idx = pd.bdate_range("2021-01-04", periods=4)
        a = pd.DataFrame({"equity": [100.0, 120.0, 90.0, 110.0], "invested": [0, 50, 50, 0]}, index=idx)
        b = pd.DataFrame({"equity": [100.0, 100.0, 100.0, 100.0], "invested": [0, 0, 0, 0]}, index=idx)
        cycles = [{"exit_date": "20210105", "r_net": -1.0}, {"exit_date": "20210106", "r_net": -1.0},
                  {"exit_date": "20210107", "r_net": 2.0}]
        m = sp.portfolio_metrics([a, b], cycles, "2021-01-04", "2021-01-07")
        self.assertEqual(m["swings"], 2)
        self.assertAlmostEqual(m["mdd"], round((220 - 190) / 220, 4))          # 합산 220 → 190
        self.assertEqual(m["longest_losing_streak"], 2)
        self.assertEqual(m["trades"], 3)
        self.assertAlmostEqual(m["win_rate"], round(1 / 3, 3))


if __name__ == "__main__":
    unittest.main()
