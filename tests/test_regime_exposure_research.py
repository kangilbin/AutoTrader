"""시장 상태 노출 조절 연구 — 신호 인과성·지연, 엔진의 배율 적용 검증"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))

import portfolio_sim as ps  # noqa: E402
import regime_exposure as rx  # noqa: E402


def _close(n=700, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, n))), index=idx)


def test_weak_series_do_not_depend_on_future():
    c = _close()
    for sig, param in rx.SIGNALS.items():
        full = rx.weak_series(c, sig, param)
        for cut in (300, 450, 600):
            part = rx.weak_series(c.iloc[:cut], sig, param)
            assert part.iloc[-1] == full.iloc[cut - 1], (sig, cut)
        altered = c.copy()
        altered.iloc[500:] *= 0.3
        assert rx.weak_series(altered, sig, param).iloc[:500].equals(full.iloc[:500]), sig


def test_g1_matches_definition():
    c = _close()
    w = rx.weak_series(c, "G1", 200)
    expected = c < c.rolling(200).mean()
    assert (w == expected.fillna(False)).all()
    assert not w.iloc[:199].any()          # 워밍업 구간은 약세가 아니다


def test_lagged_state_uses_previous_close():
    idx = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"])
    weak = pd.Series([False, True, False, False], index=idx)     # 1/3 종가에서 약세
    dates = [pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-03"), pd.Timestamp("2020-01-04"),
             pd.Timestamp("2020-01-06"), pd.Timestamp("2020-01-07")]
    out = rx.lagged_state(weak, dates)
    # 1/3 당일은 아직 전일(1/2) 기준=정상, 1/4·1/6 은 1/3 종가 기준=약세, 1/7 은 1/6 종가 기준=정상
    assert list(out) == [False, False, True, True, False]


def _one_trade_sim(scale):
    dates = pd.bdate_range("2020-03-02", periods=6)
    closes = {("NAS", "AAA"): pd.Series(100.0, index=dates)}
    t = ps.UnitTrade(market="US", code="AAA", mrkt_code="NAS", entry_date=dates[1], entry_price=100.0,
                     initial_stop=90.0, conviction=1.0, adv=1e12,
                     events=[(dates[3], ps.EVENT_EXIT, 100.0, 1.0)])
    p = ps.SimParams(mode="pool", equity0=1_000_000, risk_pct=0.01, max_position_pct=0.5,
                     max_positions=10, exposure_scale=scale)
    return ps.simulate([t], closes, p, str(dates[0].date()), str(dates[-1].date())), dates


def test_scale_none_is_unchanged_and_scales_quantity():
    base, dates = _one_trade_sim(None)
    assert base.cycles[0]["qty"] == 1000                       # 1,000,000 × 1% ÷ 10
    half, _ = _one_trade_sim({d: 0.5 for d in dates})
    assert half.cycles[0]["qty"] == 500                        # 위험 비율 ×0.5
    ones, _ = _one_trade_sim({d: 1.0 for d in dates})
    assert ones.cycles[0]["qty"] == base.cycles[0]["qty"]


def test_scale_zero_blocks_new_entries_only():
    res, dates = _one_trade_sim({d: 0.0 for d in dates_for_zero()})
    assert res.cycles == [] and res.skipped[ps.SKIP_REGIME] == 1


def dates_for_zero():
    return pd.bdate_range("2020-03-02", periods=6)
