"""일목균형표(SWING_TYPE='I') — 공통 규칙, 백테스트, 연구 코드와의 패리티"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.domain.swing.backtest.strategy_factory import StrategyFactory
from app.domain.swing.backtest.strategies.base_strategy import fill_buy, fill_sell
from app.domain.swing.entity import SwingTrade
from app.domain.swing.trading.strategies.base_ichimoku import BaseIchimokuStrategy as Base

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))
import ichimoku_popular as research  # noqa: E402


def _bars(n=900, seed=1, start="2017-01-02", trend=0.0008) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(trend, 0.02, n)))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.008, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.008, n)))
    dates = pd.bdate_range(start, periods=n).strftime("%Y%m%d")
    return pd.DataFrame({"STCK_BSOP_DATE": dates, "STCK_OPRC": open_, "STCK_HGPR": high, "STCK_LWPR": low,
                         "STCK_CLPR": close, "ACML_VOL": np.full(n, 1_000_000.0)})


# ---------- 공통 규칙 ----------

def test_frame_matches_research_b0_x2_exactly():
    """실전·백테스트의 규칙 모듈이 연구에서 검증한 B0(진입)·X2(청산)와 같은 신호를 낸다"""
    for seed in range(5):
        df = _bars(900, seed)
        frame = Base.compute_frame(df)
        arr = research.build_arrays(df)
        assert (frame["entry"].to_numpy() == arr["B0"]).all(), seed
        assert (frame["exit"].to_numpy() == arr["X2"]).all(), seed
        assert frame["entry"].sum() > 0 and frame["exit"].sum() > 0


def test_signals_do_not_depend_on_future_bars():
    df = _bars()
    full = Base.compute_frame(df)
    for cut in (200, 333, 520, 700):
        part = Base.compute_frame(df.iloc[:cut])
        for col in ("entry", "exit", "state", "cloud_bot"):
            a, b = part[col].iloc[-1], full[col].iloc[cut - 1]
            assert a == b or (pd.isna(a) and pd.isna(b)), (col, cut)


def test_entry_requires_state_and_newly_established():
    df = _bars(1400, seed=4)
    f = Base.compute_frame(df)
    c = df["STCK_CLPR"].astype(float).reset_index(drop=True)
    for t in np.flatnonzero(f["entry"].to_numpy()):
        assert c[t] > f["cloud_top"][t] and f["tenkan"][t] > f["kijun"][t]
        assert not f["state"].iloc[t - Base.RECENT_DAYS:t].all()            # 최근 5일 모두 이미 참이던 상태는 제외
    for t in np.flatnonzero(f["exit"].to_numpy()):
        assert c[t] < f["cloud_bot"][t]


def test_snapshot_reports_last_completed_bar_and_none_when_short():
    df = _bars(300)
    snap = Base.snapshot(df)
    f = Base.compute_frame(df).iloc[-1]
    assert snap["ichi_bot"] == pytest.approx(f["cloud_bot"]) and snap["ichi_entry"] == bool(f["entry"])
    assert Base.snapshot(_bars(Base.MIN_BARS - 1)) is None
    assert Base.snapshot(None) is None


# ---------- 백테스트 ----------

def _run(df, eval_start="2019-06-03", init=10_000_000, **extra):
    params = {"st_code": "AAA", "mrkt_code": "NAS", "swing_type": "I", "init_amount": init, "eval_start": eval_start, **extra}
    return StrategyFactory.get_strategy("I").compute(df.copy(), params)


def test_factory_registers_ichimoku_type_with_min_bars():
    assert "I" in StrategyFactory.get_available_strategies()
    assert StrategyFactory.get_strategy("I").min_bars({}) == Base.MIN_BARS
    assert StrategyFactory.get_strategy("S").name != StrategyFactory.get_strategy("I").name


def test_backtest_trades_match_research_fills():
    """진입·청산 날짜와 체결가가 연구의 extract_trades 와 같다 (t 종가 판정 → t+1 시가 체결, 같은 슬리피지)"""
    df = _bars(1100, seed=7)
    result = _run(df, eval_start="2019-03-04")
    arr = research.build_arrays(df)
    start, end = "2019-03-04", df["STCK_BSOP_DATE"].iloc[-1]
    end = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    expected = research.extract_trades(arr, "NAS", "AAA", "B0-X2", start, end)
    buys = [t for t in result["trades"] if t["action"] == "BUY"]
    assert len(buys) == len(expected) > 0
    for b, e in zip(buys, expected):
        assert pd.Timestamp(b["date"]) == e.entry_date
        assert b["price"] == pytest.approx(e.entry_price)
    sells = [t for t in result["trades"] if t["action"] == "SELL" and "최종 청산" not in " ".join(t["reasons"])]
    exits = [e for e in expected if e.events[-1][1] == research.ps.EVENT_EXIT]
    assert len(sells) == len(exits)
    for s, e in zip(sells, exits):
        assert pd.Timestamp(s["date"]) == e.events[-1][0]
        assert s["price"] == pytest.approx(e.events[-1][2])


def test_backtest_basic_invariants_and_chart_payload():
    result = _run(_bars(1100, seed=3))
    trades = result["trades"]
    assert trades and trades[0]["action"] == "BUY"
    assert all(a["action"] != b["action"] for a, b in zip(trades, trades[1:]))             # 매수·매도가 번갈아 나온다
    assert trades[-1]["action"] == "SELL"                                                    # 마지막은 청산(최종 평가 포함)
    assert result["parameters"]["SWING_TYPE"] == "I" and result["strategy_name"] == "일목균형표 전략"
    assert result["ichimoku_history"] and set(result["ichimoku_history"][0]) == {
        "STCK_BSOP_DATE", "tenkan", "kijun", "cloud_top", "cloud_bot"}
    assert result["price_history"][0]["STCK_BSOP_DATE"] == result["ichimoku_history"][0]["STCK_BSOP_DATE"]


def test_backtest_invests_entry_pct_and_respects_full_entry():
    df = _bars(1100, seed=3)
    normal = _run(df)["trades"][0]
    full = _run(df, full_entry=True)["trades"][0]
    assert normal["amount"] <= 10_000_000 * 0.95 and normal["amount"] > 10_000_000 * 0.90
    assert full["amount"] > normal["amount"] and full["amount"] <= 10_000_000


def test_backtest_no_trades_before_eval_start_and_no_lookahead_on_entry():
    df = _bars(1100, seed=3)
    result = _run(df, eval_start="2019-06-03")
    assert all(pd.Timestamp(t["date"]) >= pd.Timestamp("2019-06-03") for t in result["trades"])
    first_buy = result["trades"][0]
    idx = df.index[df["STCK_BSOP_DATE"] == pd.Timestamp(first_buy["date"]).strftime("%Y%m%d")][0]
    assert first_buy["price"] == pytest.approx(fill_buy(float(df["STCK_OPRC"].iloc[idx]), "NAS"))   # 체결은 신호 다음 봉 시가


# ---------- 엔티티 ----------

def test_entity_accepts_ichimoku_type_and_rejects_unknown():
    s = SwingTrade.create("5020627801", "NAS", "AAPL", "I")
    assert s.SWING_TYPE == "I" and not s.is_momentum()
    with pytest.raises(Exception):
        SwingTrade.create("5020627801", "NAS", "AAPL", "Z")
