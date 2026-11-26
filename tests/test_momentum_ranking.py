"""상대 모멘텀 순수 함수 — 점수 정의, 순위, 교체 계획, 수량, 그리고 연구 코드와의 패리티"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.domain.momentum import ranking as rk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))
import portfolio_sim as ps  # noqa: E402
import relative_momentum as rm  # noqa: E402


# ---------- 점수 ----------

def test_score_definition():
    closes = list(range(1, 301))                      # 1..300, 마지막 = 300
    expected = closes[-1 - 21] / closes[-1 - 126] - 1
    assert rk.momentum_score(closes) == pytest.approx(expected)
    assert rk.momentum_score(closes, lookback=252) == pytest.approx(closes[-22] / closes[-253] - 1)


def test_score_needs_enough_history_and_valid_prices():
    assert rk.momentum_score([100.0] * 126) is None             # lookback+1 개 필요
    assert rk.momentum_score([100.0] * 127) == pytest.approx(0.0)
    bad = [100.0] * 127
    bad[0] = 0.0
    assert rk.momentum_score(bad) is None                       # 과거 종가가 0
    nan = [100.0] * 127
    nan[-1 - 21] = float("nan")
    assert rk.momentum_score(nan) is None


def test_score_ignores_most_recent_month():
    base = [100.0] * 127
    spike = base[:-21] + [500.0] * 21                           # 최근 21봉이 폭등해도 점수에 반영되지 않는다
    assert rk.momentum_score(spike) == rk.momentum_score(base)


# ---------- 순위 ----------

def _universe(n=30, bars=200, seed=0):
    rng = np.random.default_rng(seed)
    return {f"S{i:02d}": list(100 * np.exp(np.cumsum(rng.normal(0.001 * (i - n / 2) / n, 0.01, bars)))) for i in range(n)}


def test_rank_orders_by_score_descending_and_tiebreaks_by_code():
    u = _universe()
    r = rk.rank_universe(u)
    assert [x.rank for x in r] == list(range(1, len(r) + 1))
    assert all(a.score >= b.score for a, b in zip(r, r[1:]))
    tie = {c: [100.0] * 200 for c in ("B", "A", "C")}
    tie.update({f"X{i}": list(np.linspace(100, 150, 200)) for i in range(20)})
    ranked = rk.rank_universe(tie)
    flat = [x.code for x in ranked if x.code in ("A", "B", "C")]
    assert flat == ["A", "B", "C"]                              # 동점은 코드순


def test_rank_returns_empty_when_universe_too_small():
    u = _universe(n=19)
    assert rk.rank_universe(u) == []
    assert rk.rank_universe(_universe(n=20)) != []
    u2 = _universe(n=25)
    for code in list(u2)[:6]:
        u2[code] = u2[code][:50]                                 # 이력 부족 종목은 순위에서 빠진다
    assert len(rk.rank_universe(u2)) == 19 or rk.rank_universe(u2) == []


# ---------- 교체 계획 ----------

def test_plan_keeps_continuing_holdings_untouched():
    ranked = rk.rank_universe(_universe())
    top = [r.code for r in ranked[:10]]
    plan = rk.plan_rebalance(held=top[:7], ranked=ranked)
    assert plan.target == top and plan.sells == [] and plan.buys == top[7:]


def test_plan_sells_only_dropouts_and_buys_new_entrants_in_rank_order():
    ranked = rk.rank_universe(_universe())
    top = [r.code for r in ranked[:10]]
    outside = [r.code for r in ranked[10:13]]
    plan = rk.plan_rebalance(held=top[:5] + outside, ranked=ranked)
    assert plan.sells == sorted(outside)
    assert plan.buys == top[5:]
    assert set(plan.backfill).isdisjoint(set(top[:5] + outside))
    assert plan.backfill == [r.code for r in ranked[10:20] if r.code not in outside]


def test_plan_with_empty_ranking_changes_nothing():
    plan = rk.plan_rebalance(held=["A", "B"], ranked=[])
    assert plan == rk.RebalancePlan([], [], [], [])


# ---------- 수량 ----------

def test_size_order_full_target_when_cash_is_enough():
    qty = rk.size_order(equity=1_000_000, cash=1_000_000, price=1_000)
    assert qty == 95                                            # 9.5% / 1,000원


def test_size_order_partial_fill_when_cash_is_short_but_above_ratio():
    # 목표 95주, 현금으로 살 수 있는 수량 = 60주(≥ 47) → 60주
    qty = rk.size_order(equity=1_000_000, cash=60_700, price=1_000)
    assert qty == 60
    # 현금이 목표의 절반에 못 미치면 사지 않는다 (예: 살 수 있는 40주 < 47)
    assert rk.size_order(equity=1_000_000, cash=40_500, price=1_000) == 0


def test_size_order_reduces_to_liquidity_cap_instead_of_skipping():
    qty = rk.size_order(equity=1_000_000_000, cash=1_000_000_000, price=1_000, adv=100_000_000)
    assert qty == 5_000                                         # adv 5% = 5,000,000원 / 1,000원 (목표 95,000주보다 작아 줄어든다)


def test_size_order_zero_when_price_exceeds_target_amount_or_inputs_invalid():
    assert rk.size_order(equity=100_000, cash=100_000, price=20_000) == 0      # 목표 9,500원 < 1주
    assert rk.size_order(equity=0, cash=100, price=10) == 0
    assert rk.size_order(equity=100, cash=100, price=0) == 0


# ---------- 연구 코드와의 패리티 ----------

def _research_data(n_stocks=30, n=500, seed=1, start="2018-01-02"):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n).strftime("%Y%m%d")
    data = {}
    for i in range(n_stocks):
        drift = rng.normal(0.0006, 0.0008)
        close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
        open_ = close * (1 + rng.normal(0, 0.004, n))
        data[("NAS", f"S{i:02d}")] = pd.DataFrame({
            "STCK_BSOP_DATE": dates, "STCK_OPRC": open_, "STCK_CLPR": close,
            "STCK_HGPR": np.maximum(open_, close) * 1.003, "STCK_LWPR": np.minimum(open_, close) * 0.997,
            "ACML_VOL": np.full(n, 5_000_000.0)})
    return data


def test_parity_scores_and_top10_match_research_every_month():
    data = _research_data()
    panel = rm.make_panel(data, list(data), "2030-12-31")
    close = panel["close"]
    checked = 0
    for r in panel["reb"]:
        if r < 160:
            continue
        research_scores = panel["score"]["M2"].iloc[r].dropna()
        live = {col.split("|")[1]: list(close[col].iloc[:r + 1].dropna()) for col in close.columns}
        ranked = rk.rank_universe(live)
        live_scores = {x.code: x.score for x in ranked}
        for col, s in research_scores.items():
            assert live_scores[col.split("|")[1]] == pytest.approx(s)
        research_top = [c.split("|")[1] for c in research_scores.nlargest(rm.TOP_N).index]
        assert [x.code for x in ranked[:rm.TOP_N]] == research_top
        checked += 1
    assert checked >= 6


def test_parity_bar_cleaning_matches_research():
    rng = np.random.default_rng(5)
    n = 400
    dates = pd.bdate_range("2019-01-02", periods=n).strftime("%Y%m%d")
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.012, n)))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    df = pd.DataFrame({"STCK_BSOP_DATE": dates, "STCK_OPRC": open_, "STCK_CLPR": close,
                       "STCK_HGPR": np.maximum(open_, close) * 1.002, "STCK_LWPR": np.minimum(open_, close) * 0.998,
                       "ACML_VOL": np.full(n, 1_000_000.0)})
    df.loc[200:, ["STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR"]] /= 20.0        # 20:1 분할(미보정)
    df.loc[300, "STCK_LWPR"] = float(df.loc[300, "STCK_LWPR"]) * 0.2                    # 오류 봉
    mine = rk.clean_bars(df)
    theirs = ps.adjust_splits(ps.repair_bars(df.sort_values("STCK_BSOP_DATE").reset_index(drop=True)))
    for col in ("STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR", "ACML_VOL"):
        assert np.allclose(mine[col].to_numpy(), theirs[col].to_numpy())
    assert mine["STCK_CLPR"].iloc[0] == pytest.approx(close[0] / 20.0, rel=0.05) or mine["STCK_CLPR"].iloc[0] < close[0]


def test_split_adjustment_prevents_false_momentum():
    """미보정 20:1 분할이 있는 종목의 점수는 보정 후에야 정상이다"""
    n = 400
    dates = pd.bdate_range("2019-01-02", periods=n).strftime("%Y%m%d")
    close = np.linspace(100, 130, n)
    close_split = close.copy()
    close_split[330:] = close_split[330:] / 20.0               # 점수의 두 기준 봉(273·378번째) 사이인 330번째 봉부터 20:1 분할
    df = pd.DataFrame({"STCK_BSOP_DATE": dates, "STCK_OPRC": close_split, "STCK_CLPR": close_split,
                       "STCK_HGPR": close_split, "STCK_LWPR": close_split, "ACML_VOL": np.full(n, 1e6)})
    raw = rk.momentum_score(list(df["STCK_CLPR"]))
    fixed = rk.momentum_score(list(rk.clean_bars(df)["STCK_CLPR"]))
    assert raw < -0.9                                           # 보정 안 하면 가짜 −90%
    assert fixed == pytest.approx(close[-22] / close[-127] - 1, rel=0.02)
