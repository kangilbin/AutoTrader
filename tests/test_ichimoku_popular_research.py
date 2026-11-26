"""대중적 일목 연구 — 신호 정의·인과성·구간 독립 재생·체결 타이밍 검증"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))

import ichimoku_popular as ipop  # noqa: E402
import portfolio_sim as ps  # noqa: E402
from app.domain.swing.backtest.strategies.base_strategy import fill_buy, fill_sell  # noqa: E402

SIGS = ("A0", "A1", "B0", "B1", "C", "X1", "X2", "X3")


def _bars(n=1100, seed=1, start="2017-01-02") -> pd.DataFrame:
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
    full = ipop.build_arrays(df)
    for cut in (220, 400, 650, 900):
        part = ipop.build_arrays(df.iloc[:cut])
        for k in SIGS:
            assert part[k][cut - 1] == full[k][cut - 1], (k, cut)


def test_changing_future_does_not_change_past():
    df = _bars()
    base = ipop.build_arrays(df)
    altered = df.copy()
    altered.loc[600:, ["STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR"]] *= 3.0
    after = ipop.build_arrays(altered)
    for k in SIGS:
        assert (base[k][:600] == after[k][:600]).all(), k


def test_signal_definitions_over_many_seeds():
    seen = {k: 0 for k in SIGS}
    for seed in range(6):
        df = _bars(1400, seed)
        d = df.sort_values("STCK_BSOP_DATE").reset_index(drop=True)
        h, lo, c, o = (d[k].astype(float) for k in ("STCK_HGPR", "STCK_LWPR", "STCK_CLPR", "STCK_OPRC"))
        tenkan = (h.rolling(9).max() + lo.rolling(9).min()) / 2
        kijun = (h.rolling(26).max() + lo.rolling(26).min()) / 2
        sa = ((tenkan + kijun) / 2).shift(26)
        sb = ((h.rolling(52).max() + lo.rolling(52).min()) / 2).shift(26)
        top, bot = np.maximum(sa, sb), np.minimum(sa, sb)
        a = ipop.build_arrays(df)
        for t in np.flatnonzero(a["A0"]):
            assert c[t] > top[t] and c[t - 1] <= top[t - 1] and c[t] > c[t - 26]
        for t in np.flatnonzero(a["A1"]):
            assert a["A0"][t] and sa[t] > sb[t]
        for t in np.flatnonzero(a["B0"]):
            assert c[t] > top[t] and tenkan[t] > kijun[t]
        for t in np.flatnonzero(a["B1"]):
            assert sa[t] > sb[t] and c[t] > top[t] and tenkan[t] > kijun[t]
        for t in np.flatnonzero(a["C"]):
            assert sa[t] > sb[t] and c[t] > top[t] and kijun[t] > kijun[t - 5] and lo[t] <= kijun[t] and c[t] > kijun[t] and c[t] > o[t]
        for t in np.flatnonzero(a["X1"]):
            assert c[t] < top[t]
        for t in np.flatnonzero(a["X2"]):
            assert c[t] < bot[t]
        for t in np.flatnonzero(a["X3"]):
            assert sa[t] < sb[t] or c[t] < bot[t]
        for k in SIGS:
            seen[k] += int(a[k].sum())
    assert all(v > 0 for v in seen.values()), seen                  # 항상 False 인 고장 방지


def test_b_state_excludes_already_established_state():
    """B0 는 최근 5일이 모두 참이던(오래 유지된) 상태에서는 나오지 않는다"""
    df = _bars(1400, seed=3)
    a = ipop.build_arrays(df)
    d = df.sort_values("STCK_BSOP_DATE").reset_index(drop=True)
    h, lo, c = (d[k].astype(float) for k in ("STCK_HGPR", "STCK_LWPR", "STCK_CLPR"))
    tenkan = (h.rolling(9).max() + lo.rolling(9).min()) / 2
    kijun = (h.rolling(26).max() + lo.rolling(26).min()) / 2
    sa = ((tenkan + kijun) / 2).shift(26)
    sb = ((h.rolling(52).max() + lo.rolling(52).min()) / 2).shift(26)
    state = (c > np.maximum(sa, sb)) & (tenkan > kijun)
    for t in np.flatnonzero(a["B0"]):
        assert not state.iloc[t - 5:t].all()


def test_extract_window_timing_and_isolation():
    df = _bars(1500, seed=7)
    arr = ipop.build_arrays(df)
    start, end = "2020-03-02", "2021-06-30"
    for cand in ("A1-X2", "B0-X1", "C-X3"):
        ts = ipop.extract_trades(arr, "NAS", "AAA", cand, start, end)
        assert ts, cand
        entry, exit_ = cand.split("-")
        prev_exit = None
        last_date = pd.Timestamp(arr["dates"][np.searchsorted(arr["dates"], np.datetime64(end), side="right") - 1])
        for t in ts:
            ei = int(np.flatnonzero(arr["dates"] == np.datetime64(t.entry_date))[0])
            assert arr[entry][ei - 1]                                            # 신호는 전일 종가
            assert t.entry_price == fill_buy(float(arr["o"][ei]), "NAS")         # 체결은 당일 시가
            assert t.entry_date >= pd.Timestamp(start) and t.initial_stop < t.entry_price
            exit_date, kind, price, _ = t.events[-1]
            assert exit_date <= last_date and exit_date > t.entry_date
            xi = int(np.flatnonzero(arr["dates"] == np.datetime64(exit_date))[0])
            if kind == ps.EVENT_EXIT:
                assert arr[exit_][xi - 1] and price == fill_sell(float(arr["o"][xi]), "NAS")
            else:
                assert exit_date == last_date and price == fill_sell(float(arr["c"][xi]), "NAS")   # 구간 끝 종가 평가
            if prev_exit is not None:
                assert t.entry_date >= prev_exit
            prev_exit = exit_date


def test_roll_control_stays_in_window_and_keeps_count():
    df = _bars(1500, seed=9)
    arr = ipop.build_arrays(df)
    start, end = "2020-03-02", "2021-12-31"
    lo, hi, _ = ipop.window_range(arr, start, end)
    seg = arr["A0"][lo:hi + 1]
    assert int(np.roll(seg, 17).sum()) == int(seg.sum())
    base = ipop.extract_trades(arr, "NAS", "AAA", "A0-X2", start, end)
    rolled = ipop.extract_trades(arr, "NAS", "AAA", "A0-X2", start, end, roll_seed=3)
    assert all(pd.Timestamp(start) <= t.entry_date <= pd.Timestamp(end) for t in rolled)
    assert rolled != base or not base
