"""표준 일목 연구 — 신호 정의·인과성·체결 타이밍 검증"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))

import ichimoku_standard as isd  # noqa: E402
import portfolio_sim as ps  # noqa: E402
from app.domain.swing.backtest.strategies.base_strategy import fill_buy, fill_sell  # noqa: E402


def _bars(n=900, seed=1, start="2017-01-02") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0008, 0.02, n)))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.008, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.008, n)))
    dates = pd.bdate_range(start, periods=n).strftime("%Y%m%d")
    return pd.DataFrame({"STCK_BSOP_DATE": dates, "STCK_OPRC": open_, "STCK_HGPR": high, "STCK_LWPR": low,
                         "STCK_CLPR": close, "ACML_VOL": np.full(n, 1_000_000.0)})


def test_signals_do_not_depend_on_future_bars():
    df = _bars()
    full = isd.build_arrays(df)
    for cut in (200, 333, 520, 700):
        part = isd.build_arrays(df.iloc[:cut])
        for k in ("E1", "E2", "X1", "X2", "X3"):
            assert part[k][cut - 1] == full[k][cut - 1], (k, cut)


def test_changing_future_does_not_change_past():
    df = _bars()
    base = isd.build_arrays(df)
    altered = df.copy()
    altered.loc[500:, ["STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR"]] *= 3.0
    after = isd.build_arrays(altered)
    for k in ("E1", "E2", "X1", "X2", "X3"):
        assert (base[k][:500] == after[k][:500]).all(), k


def test_e1_requires_all_three_conditions_and_first_day_only():
    df = _bars(1500, seed=4)
    d = df.sort_values("STCK_BSOP_DATE").reset_index(drop=True)
    h, lo, c = (d[k].astype(float) for k in ("STCK_HGPR", "STCK_LWPR", "STCK_CLPR"))
    tenkan = (h.rolling(9).max() + lo.rolling(9).min()) / 2
    kijun = (h.rolling(26).max() + lo.rolling(26).min()) / 2
    sa = ((tenkan + kijun) / 2).shift(26)
    sb = ((h.rolling(52).max() + lo.rolling(52).min()) / 2).shift(26)
    top = np.maximum(sa, sb)
    a = isd.build_arrays(df)
    idx = np.flatnonzero(a["E1"])
    assert len(idx) > 0
    for t in idx:
        assert tenkan[t] > kijun[t] and c[t] > top[t] and c[t] > c[t - 26]
        prev_all = tenkan[t - 1] > kijun[t - 1] and c[t - 1] > top[t - 1] and c[t - 1] > c[t - 26 - 1]
        assert not prev_all                    # 전일에는 세 조건이 모두 충족되지 않았다


def test_x3_requires_all_three_bearish():
    df = _bars(1500, seed=5)
    a = isd.build_arrays(df)
    idx = np.flatnonzero(a["X3"])
    assert len(idx) > 0
    assert (a["X1"][idx]).all()                # 삼역역전이면 종가 < 구름 하단도 성립


def test_extract_trades_fill_next_open_and_no_overlap():
    df = _bars(1400, seed=7)
    arr = isd.build_arrays(df)
    start = np.datetime64("2019-01-01")
    for cand in ("E1-X1", "E2-X2", "E1-X3"):
        ts = isd.extract_trades(arr, "NAS", "AAA", cand, start)
        assert ts, cand
        prev_exit = None
        for t in ts:
            ei = int(np.flatnonzero(arr["dates"] == np.datetime64(t.entry_date))[0])
            assert arr["E1" if cand.startswith("E1") else "E2"][ei - 1]           # 신호는 전일 종가
            assert t.entry_price == fill_buy(float(arr["o"][ei]), "NAS")          # 체결은 당일 시가
            assert arr["dates"][ei] >= start and t.initial_stop < t.entry_price
            exit_date = t.events[-1][0]
            assert exit_date > t.entry_date
            if t.events[-1][1] == ps.EVENT_EXIT:
                xi = int(np.flatnonzero(arr["dates"] == np.datetime64(exit_date))[0])
                x = arr[cand.split("-")[1]]
                assert x[xi - 1]                                                    # 청산 신호는 전일 종가
                assert t.events[-1][2] == fill_sell(float(arr["o"][xi]), "NAS")     # 체결은 당일 시가
            if prev_exit is not None:
                assert t.entry_date >= prev_exit                                    # 포지션 겹침 없음
            prev_exit = exit_date


def test_roll_control_keeps_signal_count():
    arr = isd.build_arrays(_bars(1400, seed=9))
    a = int(arr["E1"].sum())
    assert int(np.roll(arr["E1"], 123).sum()) == a
