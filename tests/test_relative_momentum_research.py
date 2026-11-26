"""상대 모멘텀 연구 — 점수 인과성, 보유 집단 규칙, 체결 타이밍, 라벨 셔플, 사이징 검증"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))

import portfolio_sim as ps  # noqa: E402
import relative_momentum as rm  # noqa: E402
from app.domain.swing.backtest.strategies.base_strategy import fill_buy, fill_sell  # noqa: E402


def _data(n_stocks=30, n=700, seed=1, start="2018-01-02"):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n).strftime("%Y%m%d")
    data = {}
    for i in range(n_stocks):
        drift = rng.normal(0.0006, 0.0006)
        close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
        open_ = close * (1 + rng.normal(0, 0.004, n))
        data[("NAS", f"S{i:02d}")] = pd.DataFrame({
            "STCK_BSOP_DATE": dates, "STCK_OPRC": open_, "STCK_CLPR": close,
            "STCK_HGPR": np.maximum(open_, close), "STCK_LWPR": np.minimum(open_, close),
            "ACML_VOL": np.full(n, 5_000_000.0)})
    return data


def _panel(data=None, end="2020-12-31"):
    data = data or _data()
    return rm.make_panel(data, list(data), end)


def test_score_does_not_depend_on_future():
    data = _data()
    full = rm.make_panel(data, list(data), "2030-12-31")
    cut_end = "2019-06-28"
    cut = rm.make_panel(data, list(data), cut_end)
    r = int(cut["reb"][-1] - 1) if len(cut["reb"]) else 0
    for cand in rm.CANDS:
        a = cut["score"][cand].iloc[r]
        b = full["score"][cand].iloc[r]
        assert np.allclose(a.fillna(-9).to_numpy(), b.fillna(-9).to_numpy())


def test_score_definition():
    p = _panel()
    c = p["close"]
    r = 300
    expected = c.iloc[r - 21] / c.iloc[r - 252] - 1
    assert np.allclose(p["score"]["M1"].iloc[r].to_numpy(), expected.to_numpy())
    expected2 = c.iloc[r - 21] / c.iloc[r - 126] - 1
    assert np.allclose(p["score"]["M2"].iloc[r].to_numpy(), expected2.to_numpy())


def test_holding_rules_and_timing():
    data = _data()
    p = _panel(data)
    start, end = "2019-06-03", "2020-12-31"
    ts = rm.build_trades(p, "M1", start, end)
    assert ts
    idx = p["close"].index
    last = idx[int(idx.searchsorted(pd.Timestamp(end), side="right")) - 1]
    by_code = {}
    for t in ts:
        by_code.setdefault(t.code, []).append(t)
        ei = idx.get_loc(t.entry_date)
        r = ei - 1
        assert r in set(p["reb"])                                           # 진입은 월말 다음 거래일
        assert t.entry_price == fill_buy(float(p["open"][f"NAS|{t.code}"].iloc[ei]), "NAS")   # 시가 체결
        assert t.entry_date >= pd.Timestamp(start)
        d, kind, price, _ = t.events[-1]
        assert d > t.entry_date and d <= last
        if kind == ps.EVENT_EXIT:
            xi = idx.get_loc(d)
            assert xi - 1 in set(p["reb"]) and price == fill_sell(float(p["open"][f"NAS|{t.code}"].iloc[xi]), "NAS")
        else:
            assert d == last
    for code, lst in by_code.items():                                       # 같은 종목 기간이 겹치지 않는다
        lst.sort(key=lambda t: t.entry_date)
        for a, b in zip(lst, lst[1:]):
            assert b.entry_date >= a.events[-1][0]
    # 동시 보유 종목 수는 TOP_N 을 넘지 않는다
    days = pd.date_range(start, end, freq="B")
    for d in days[::7]:
        n = sum(1 for t in ts if t.entry_date <= d < t.events[-1][0])
        assert n <= rm.TOP_N


def test_unchanged_top_holdings_are_not_traded():
    """상위 집단이 그대로면 거래가 없다 — 사이클 수는 월 수 × TOP_N 보다 훨씬 적다"""
    p = _panel()
    ts = rm.build_trades(p, "M2", "2019-06-03", "2020-12-31")
    months = len([r for r in p["reb"] if pd.Timestamp("2019-06-03") <= p["close"].index[r + 1] <= pd.Timestamp("2020-12-31")])
    assert len(ts) < months * rm.TOP_N


def test_shuffle_control_changes_selection_not_structure():
    p = _panel()
    start, end = "2019-06-03", "2020-12-31"
    base = rm.build_trades(p, "M1", start, end)
    perm = np.random.default_rng(0).permutation(p["close"].shape[1])
    ctrl = rm.build_trades(p, "M1", start, end, perm)
    assert {t.code for t in base} != {t.code for t in ctrl}               # 다른 종목을 고른다
    assert abs(len(ctrl) - len(base)) <= len(base) * 0.6 + 5              # 회전 구조는 비슷한 규모


def test_sizing_cap_binds_at_95_percent_of_pool():
    """인위적 손절선 설정에서 종목당 최대 비중(9.5%)이 사이징을 결정한다"""
    dates = pd.bdate_range("2020-03-02", periods=8)
    closes = {("NAS", "AAA"): pd.Series(100.0, index=dates)}
    entry = fill_buy(100.0, "NAS")
    t = ps.UnitTrade(market="US", code="AAA", mrkt_code="NAS", entry_date=dates[1], entry_price=entry,
                     initial_stop=entry * rm.ARTIFICIAL_STOP, conviction=1.0, adv=1e12,
                     events=[(dates[5], ps.EVENT_EXIT, 100.0, 1.0)])
    p = ps.SimParams(equity0=1_000_000, **rm.SIM_KW)
    res = ps.simulate([t], closes, p, str(dates[0].date()), str(dates[-1].date()))
    amount = res.cycles[0]["amount"]
    assert abs(amount / 1_000_000 - 0.095) < 0.002
    assert res.cycles[0]["binding"] == "position_cap"
