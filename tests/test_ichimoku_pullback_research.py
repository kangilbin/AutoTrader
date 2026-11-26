"""일목 눌림목 연구 스크립트 — 인과성(선견 금지) 검증

t 봉의 신호는 t 봉까지의 데이터만으로 정해져야 한다. 데이터를 t 에서 잘라 다시 계산해도 같은 값이어야 하고,
미래 봉을 바꿔도 과거 신호가 변하면 안 된다.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))

import ichimoku_pullback as ip  # noqa: E402


def _bars(n: int = 600, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0008, 0.02, n)))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.008, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.008, n)))
    dates = pd.bdate_range("2018-01-01", periods=n).strftime("%Y%m%d")
    return pd.DataFrame({"STCK_BSOP_DATE": dates, "STCK_OPRC": open_, "STCK_HGPR": high,
                         "STCK_LWPR": low, "STCK_CLPR": close})


def test_signals_do_not_depend_on_future_bars():
    cfg = ip.Cfg()
    df = _bars()
    full = ip.ichimoku_frame(df, cfg)
    for cut in (150, 260, 333, 480):
        part = ip.ichimoku_frame(df.iloc[:cut], cfg)
        a, b = part.iloc[cut - 1], full.iloc[cut - 1]
        for col in ("tenkan", "kijun", "top", "bot", "atr", "dist", "touch", "confirm"):
            assert a[col] == b[col] or (pd.isna(a[col]) and pd.isna(b[col])), (cut, col)


def test_changing_future_does_not_change_past_signals():
    cfg = ip.Cfg()
    df = _bars()
    base = ip.ichimoku_frame(df, cfg)
    altered = df.copy()
    altered.loc[400:, ["STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR"]] *= 3.0
    after = ip.ichimoku_frame(altered, cfg)
    pd.testing.assert_frame_equal(base.iloc[:400].reset_index(drop=True),
                                  after.iloc[:400].reset_index(drop=True))


def test_signals_exist_in_synthetic_uptrend():
    """신호가 아예 안 나오는 고장(항상 False)을 막는 최소 점검"""
    cfg = ip.Cfg()
    total_touch = total_confirm = 0
    for seed in range(8):
        f = ip.ichimoku_frame(_bars(900, seed), cfg)
        total_touch += int(f["touch"].sum())
        total_confirm += int(f["confirm"].sum())
    assert total_touch > 0 and total_confirm > 0


def _curve(equity, invested):
    idx = pd.bdate_range("2020-01-01", periods=len(equity))
    return pd.DataFrame({"equity": equity, "invested": invested}, index=idx)


def test_park_overlay_all_cash_tracks_etf():
    """전액 현금이면 합성 곡선 = ETF (비용 0 일 때)"""
    idx = pd.bdate_range("2020-01-01", periods=4)
    etf = pd.Series([100.0, 110.0, 121.0, 133.1], index=idx)
    curve = _curve([1000.0] * 4, [0.0] * 4)
    out = ip.park_overlay(curve, etf, 1000.0, 0.0)
    assert abs(out.iloc[-1] - 1000.0 * 133.1 / 100.0 * (1 / 1.0)) < 1e-6 * 1000 * 1.331 or abs(out.iloc[-1] - 1210.0) < 1.0
    # 첫날 수익률은 0 (ETF 의 첫 pct_change 는 없음) → 이후 3일 +10% 복리
    assert abs(out.iloc[-1] - 1000.0 * 1.1 ** 3) < 1e-6


def test_park_overlay_fully_invested_equals_strategy():
    """전액 투자면 ETF 와 무관하게 전략 곡선 그대로"""
    idx = pd.bdate_range("2020-01-01", periods=4)
    etf = pd.Series([100.0, 50.0, 200.0, 10.0], index=idx)
    curve = _curve([1010.0, 1030.0, 1000.0, 1100.0], [1010.0, 1030.0, 1000.0, 1100.0])
    out = ip.park_overlay(curve, etf, 1000.0, 0.0)
    # 첫날은 '전액 현금이었다' 가정이라 ETF 영향이 없고(첫 ETF 수익률 0), 이후는 현금 비중 0 이라 전략 그대로
    assert np.allclose(out.to_numpy(), curve["equity"].to_numpy(), rtol=1e-9)


def test_park_overlay_cost_charged_on_cash_weight_change():
    idx = pd.bdate_range("2020-01-01", periods=3)
    etf = pd.Series([100.0, 100.0, 100.0], index=idx)
    # 둘째 날 전액 투자로 전환 → 현금 비중 1→0 변화 비용
    curve = _curve([1000.0, 1000.0, 1000.0], [0.0, 1000.0, 1000.0])
    out = ip.park_overlay(curve, etf, 1000.0, 0.01)
    assert out.iloc[1] < 1000.0 and out.iloc[1] > 989.0     # 약 1% 비용
