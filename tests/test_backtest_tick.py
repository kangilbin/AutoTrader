"""백테스트 호가 단위 회귀 테스트 (국내 호가표 / 미국 $0.01)

실행:
  uv run pytest tests/test_backtest_tick.py -v

문제: 백테스트 체결가 반올림(ceil_tick/floor_tick)이 국내 호가표만 알아서,
미국 종목도 $2,000 미만이 1단위가 되어 정수 달러로 올림/내림됐다
($150.21 매수 → $151). 실전 주문은 normalize_order_price($0.01)로 나가므로
백테스트만 현실에 없는 가격으로 체결하고 있었다.
"""
import unittest

import numpy as np
import pandas as pd

from app.domain.swing.backtest.strategies.base_strategy import (
    buy_tick, ceil_tick, fill_buy, fill_sell, floor_tick, sell_tick, tick_size, trade_costs,
)
from app.domain.swing.backtest.strategies.single_ema_backtest_strategy import SingleEMABacktestStrategy


def _bars(base: float, seed: int = 0, n: int = 600) -> pd.DataFrame:
    """추세·변동이 섞인 합성 일봉 (매매가 여러 번 일어나도록)"""
    rng = np.random.default_rng(seed)
    close = base * np.exp(np.cumsum(rng.normal(0.0008, 0.022, n)))
    open_ = close * (1 + rng.normal(0, 0.006, n))
    high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.012, n)))
    low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.012, n)))
    volume = rng.integers(200_000, 900_000, n) * (1 + (np.diff(close, prepend=close[0]) > 0))
    dates = pd.bdate_range("2023-01-02", periods=n)
    # 실제 일봉은 이미 체결 가능한 가격(미국 센트 단위)이다 — 종가 체결 경로가 그대로 쓰므로 맞춰 둔다
    return pd.DataFrame({
        "STCK_BSOP_DATE": dates.strftime("%Y%m%d"), "STCK_OPRC": open_.round(2), "STCK_HGPR": high.round(2),
        "STCK_LWPR": low.round(2), "STCK_CLPR": close.round(2), "ACML_VOL": volume,
    })


def _params(mrkt_code: str) -> dict:
    return {"st_code": "X", "mrkt_code": mrkt_code, "swing_type": "S",
            "init_amount": 10_000_000, "eval_start": "2023-09-01"}


class TickHelperTest(unittest.TestCase):

    def test_us_rounds_to_cent(self):
        for mrkt in ("NAS", "NYS", "AMS"):
            with self.subTest(mrkt=mrkt):
                self.assertEqual(buy_tick(150.211, mrkt), 150.22)
                self.assertEqual(sell_tick(149.789, mrkt), 149.78)

    def test_us_sub_dollar_uses_finer_tick(self):
        self.assertEqual(buy_tick(0.12345, "NAS"), 0.1235)

    def test_domestic_matches_tick_table(self):
        """국내(또는 시장 코드 없음)는 기존 호가표 결과와 같다"""
        for price in (1_999.4, 4_321.0, 15_555.0, 123_456.0):
            for mrkt in ("J", ""):
                with self.subTest(price=price, mrkt=mrkt):
                    self.assertEqual(buy_tick(price, mrkt), ceil_tick(price))
                    self.assertEqual(sell_tick(price, mrkt), floor_tick(price))


class SingleEMABacktestTickTest(unittest.TestCase):

    def test_us_fills_keep_cents(self):
        """미국 종목 체결가가 정수 달러로 뭉개지지 않는다"""
        result = SingleEMABacktestStrategy().compute(_bars(150.0), _params("NAS"))
        prices = [t["price"] for t in result["trades"] if "최종 청산" not in t["reasons"]]
        self.assertTrue(prices, "매매가 없어 검증 불가 — 합성 데이터 확인")
        self.assertTrue(any(p != int(p) for p in prices), f"모든 체결가가 정수 달러: {prices}")
        for p in prices:
            self.assertAlmostEqual(p, round(p, 2), places=9)

    def test_domestic_uses_tick_table(self):
        """국내 체결가는 국내 호가표 단위다 (10,000원대 = 10원)"""
        result = SingleEMABacktestStrategy().compute(_bars(10_000.0, seed=3), _params("J"))
        prices = [t["price"] for t in result["trades"] if "최종 청산" not in t["reasons"]]
        self.assertTrue(prices, "매매가 없어 검증 불가 — 합성 데이터 확인")
        for p in prices:
            self.assertEqual(p % tick_size(p), 0, f"{p} 가 호가 단위가 아님")


class CostModelTest(unittest.TestCase):

    def test_slippage_one_tick_against_us(self):
        self.assertEqual(fill_buy(150.211, "NAS"), 150.23)     # 올림 150.22 + $0.01
        self.assertEqual(fill_sell(149.789, "NAS"), 149.77)    # 내림 149.78 − $0.01
        self.assertEqual(fill_buy(15_555, "J"), 15_570)        # 올림 15,560 + 10원
        self.assertEqual(fill_sell(15_555, "J"), 15_540)       # 내림 15,550 − 10원

    def test_costs_by_market(self):
        self.assertEqual(trade_costs("J"), (0.00147, 0.0020))
        self.assertEqual(trade_costs("NAS"), (0.0025, 0.0))    # 미국: 거래세 없음

    def test_us_sell_has_no_tax(self):
        trades = []
        strategy = SingleEMABacktestStrategy()
        strategy._execute_buy(trades, "d", 100.0, 10, 10_000, ["매수"], mrkt_code="NAS")
        strategy._execute_sell(trades, "d", 110.0, 9_000, ["매도"], mrkt_code="NAS")
        self.assertEqual(trades[0]["commission"], 100.0 * 10 * 0.0025)
        self.assertEqual(trades[1]["tax"], 0.0)

if __name__ == "__main__":
    unittest.main()
