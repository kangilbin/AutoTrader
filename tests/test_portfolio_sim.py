"""
포트폴리오 백테스트(연구 스크립트) 테스트

실행:
  uv run pytest tests/test_portfolio_sim.py -v

추출(전략 거래 기록 → UnitTrade)과 재생 엔진의 규칙을 고정한다. 결과 숫자가 이 변환에 전부 의존하므로
전략을 가짜로 대체해 부분익절·전량 청산·최종 청산 경로를 직접 만든다.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))

import portfolio_sim as ps  # noqa: E402


def make_df(n: int = 400, start: str = "2019-06-03") -> pd.DataFrame:
    """종가 100 고정, 거래량 = 봉 번호+1 — 평균 거래대금을 손으로 계산할 수 있다"""
    dates = pd.bdate_range(start, periods=n)
    return pd.DataFrame({
        "STCK_BSOP_DATE": dates.strftime("%Y%m%d"),
        "STCK_OPRC": 100.0, "STCK_HGPR": 101.0, "STCK_LWPR": 99.0, "STCK_CLPR": 100.0,
        "ACML_VOL": [float(i + 1) for i in range(n)],
    })


def buy(date, price=100.0, qty=10_000, stop=93.0, conviction=0.6):
    return {"date": date, "action": "BUY", "quantity": qty, "price": price,
            "initial_stop": stop, "conviction": conviction, "reasons": ["매수"]}


def sell(date, price, qty, reason):
    return {"date": date, "action": "SELL", "quantity": qty, "price": price, "reasons": [reason]}


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.df = make_df()
        self.d = pd.to_datetime(self.df["STCK_BSOP_DATE"]).tolist()

    def run_extract(self, trades, lines=()):
        result = {"trades": trades, "exit_lines": [{"date": x, "exit_line": v} for x, v in lines]}
        with patch.object(ps.SingleEMABacktestStrategy, "compute", return_value=result):
            return ps.extract_unit_trades(self.df, "J", "005930", "dev")

    def test_partial_then_exit(self):
        """절반 매도는 PARTIAL, 남은 전량 매도는 EXIT — 비율 합 1"""
        d = self.d
        trades = [buy(d[300]), sell(d[310], 150.0, 5_000, "부분익절(+50%)"), sell(d[320], 120.0, 5_000, "이익확정")]
        [t] = self.run_extract(trades)
        self.assertEqual([(e[1], e[3]) for e in t.events],
                         [(ps.EVENT_PARTIAL, 0.5), (ps.EVENT_EXIT, 0.5)])
        self.assertAlmostEqual(sum(e[3] for e in t.events), 1.0)
        self.assertEqual(t.exit_date, d[320])
        self.assertEqual((t.entry_price, t.initial_stop, t.conviction, t.market), (100.0, 93.0, 0.6, "KR"))

    def test_full_exit_without_partial(self):
        d = self.d
        [t] = self.run_extract([buy(d[300]), sell(d[305], 92.0, 10_000, "손절")])
        self.assertEqual([(e[1], e[3]) for e in t.events], [(ps.EVENT_EXIT, 1.0)])

    def test_final_liquidation_flagged(self):
        """구간 끝 미청산 포지션은 FINAL — 실제 청산이 아니라 종가 평가라는 표시"""
        d = self.d
        [t] = self.run_extract([buy(d[390]), sell(d[399], 100.0, 10_000, "최종 청산")])
        self.assertEqual(t.events[0][1], ps.EVENT_FINAL)

    def test_exit_lines_only_during_holding(self):
        """청산선은 진입 다음날부터 청산일까지만 그 사이클에 붙는다 (다른 사이클 값이 섞이지 않는다)"""
        d = self.d
        lines = [(d[299], 1.0), (d[301], 93.0), (d[303], 95.0), (d[305], 96.0), (d[310], 7.0)]
        trades = [buy(d[300]), sell(d[305], 95.0, 10_000, "본전방어")]
        [t] = self.run_extract(trades, lines)
        self.assertEqual(t.exit_lines, {d[301]: 93.0, d[303]: 95.0, d[305]: 96.0})

    def test_two_cycles_same_stock(self):
        d = self.d
        trades = [buy(d[300]), sell(d[310], 90.0, 10_000, "손절"),
                  buy(d[340], price=110.0, stop=102.0), sell(d[350], 130.0, 10_000, "이익확정")]
        cycles = self.run_extract(trades)
        self.assertEqual([c.entry_date for c in cycles], [d[300], d[340]])
        self.assertEqual(cycles[1].entry_price, 110.0)

    def test_adv_excludes_entry_day(self):
        """평균 거래대금은 진입 당일을 뺀 직전 20일 — 당일 거래량으로 유동성을 판정하는 선견을 막는다"""
        d = self.d
        i = 300
        [t] = self.run_extract([buy(d[i]), sell(d[i + 1], 99.0, 10_000, "손절")])
        expected = sum(100.0 * (k + 1) for k in range(i - 20, i)) / 20
        self.assertAlmostEqual(t.adv, expected)

    def test_insufficient_warmup_returns_empty(self):
        """구간 시작(2020-05-01) 전 봉이 MIN_WARMUP_BARS 미만이면 그 종목은 제외"""
        short = make_df(n=300, start="2020-03-02")
        with patch.object(ps.SingleEMABacktestStrategy, "compute") as comp:
            self.assertEqual(ps.extract_unit_trades(short, "J", "X", "dev"), [])
            comp.assert_not_called()

    def test_market_of(self):
        self.assertEqual(ps.market_of("J"), "KR")
        for m in ("NYS", "NAS", "AMS"):
            self.assertEqual(ps.market_of(m), "US")


def bars(closes, vols=None):
    n = len(closes)
    vols = vols or [1000.0] * n
    return pd.DataFrame({
        "STCK_BSOP_DATE": pd.bdate_range("2021-01-04", periods=n).strftime("%Y%m%d"),
        "STCK_OPRC": closes, "STCK_HGPR": [c * 1.01 for c in closes],
        "STCK_LWPR": [c * 0.99 for c in closes], "STCK_CLPR": closes, "ACML_VOL": vols,
    })


class AdjustSplitsTest(unittest.TestCase):
    """DB 일봉의 미보정 액면분할이 가짜 손절·가짜 수익(−23R, +119R)을 만들었다 — 연구 적재에서 소급 보정"""

    def test_forward_split_makes_prices_continuous(self):
        """4:1 분할(400 → 100) — 분할 전 가격을 1/4로, 거래량을 4배로"""
        out = ps.adjust_splits(bars([400.0, 404.0, 100.0, 101.0], [1000.0, 1000.0, 4000.0, 4000.0]))
        self.assertEqual(out["STCK_CLPR"].round(6).tolist(), [100.0, 101.0, 100.0, 101.0])
        self.assertEqual(out["ACML_VOL"].round(6).tolist(), [4000.0, 4000.0, 4000.0, 4000.0])
        self.assertAlmostEqual(out["STCK_HGPR"].iloc[0], 100.0 * 1.01)   # 고·저·시가도 같은 비율

    def test_reverse_split(self):
        """1:8 병합(GE) — 병합 전 가격을 8배로"""
        out = ps.adjust_splits(bars([11.28, 11.5, 90.0, 92.0], [8000.0, 8000.0, 1000.0, 1000.0]))
        self.assertAlmostEqual(out["STCK_CLPR"].iloc[0], 90.24)
        self.assertAlmostEqual(out["ACML_VOL"].iloc[0], 1000.0)

    def test_real_crash_is_not_a_split(self):
        """−36%(0.64)는 분할 비율이 아니다 — 폭락을 분할로 오인하면 손실 거래가 사라져 결과가 낙관적이 된다"""
        raw = bars([100.0, 101.0, 64.0, 65.0], [1000.0, 1000.0, 5000.0, 3000.0])
        pd.testing.assert_frame_equal(ps.adjust_splits(raw), raw)

    def test_two_splits_accumulate(self):
        out = ps.adjust_splits(bars([1000.0, 250.0, 25.0, 26.0]))   # 4:1 뒤 10:1
        self.assertEqual(out["STCK_CLPR"].round(6).tolist(), [25.0, 25.0, 25.0, 26.0])

    def test_missing_volume_is_left_alone(self):
        """거래량이 없는 봉(분사·상장 직후)은 분할인지 알 수 없어 건드리지 않는다"""
        raw = bars([100.0, 25.0, 26.0], [1000.0, float("nan"), 1000.0])
        pd.testing.assert_frame_equal(ps.adjust_splits(raw), raw)

    def test_no_split_unchanged(self):
        raw = bars([100.0, 103.0, 99.0, 101.0])
        pd.testing.assert_frame_equal(ps.adjust_splits(raw), raw)

    def test_split_ratio(self):
        self.assertEqual(ps.split_ratio(400.0, 100.0), 0.25)
        self.assertEqual(ps.split_ratio(11.28, 87.6), 8.0)          # 7.766배 — 가장 가까운 정수 8에서 3% 이내
        self.assertEqual(ps.split_ratio(100.0, 52.0), 0.5)          # 2:1 분할일 갭 −4%가 섞여도
        self.assertIsNone(ps.split_ratio(100.0, 64.0))
        self.assertIsNone(ps.split_ratio(100.0, 230.0))             # 급등(PGY형)은 분할이 아니다
        self.assertIsNone(ps.split_ratio(100.0, 150.0))             # 3:2 같은 비정수 비율은 다루지 않는다
        self.assertIsNone(ps.split_ratio(0.0, 10.0))

    def test_split_day_move_does_not_hide_split(self):
        """분할 당일 종가가 +12% 올라도(TSLA 5:1) 시가 갭으로 분할을 잡는다"""
        raw = bars([500.0, 500.0, 112.0, 113.0], [1000.0, 1000.0, 5000.0, 5000.0])
        raw.loc[2, "STCK_OPRC"] = 100.0                              # 시가는 정확히 1/5
        out = ps.adjust_splits(raw)
        self.assertAlmostEqual(out["STCK_CLPR"].iloc[1], 100.0)


class RepairBarsTest(unittest.TestCase):
    def test_absurd_low_is_clipped_to_body(self):
        """KIS 원본 오류 봉 — 저가가 몸통의 절반 미만이면 몸통 범위로 자른다 (가짜 손절 방지)"""
        df = pd.DataFrame({"STCK_OPRC": [40.0], "STCK_HGPR": [41.0], "STCK_LWPR": [10.6], "STCK_CLPR": [40.57]})
        out = ps.repair_bars(df)
        self.assertEqual(out["STCK_LWPR"].iloc[0], 40.0)

    def test_normal_bar_untouched(self):
        df = pd.DataFrame({"STCK_OPRC": [100.0], "STCK_HGPR": [105.0], "STCK_LWPR": [95.0], "STCK_CLPR": [102.0]})
        pd.testing.assert_frame_equal(ps.repair_bars(df), df)


COMM, TAX = 0.00147, 0.002          # 국내 수수료·매도 거래세 (base_strategy._TRADE_COSTS)
D = pd.bdate_range("2021-01-04", periods=40)
START, END = "2021-01-01", "2021-12-31"


def flat(price=100.0, dates=D):
    return pd.Series(price, index=dates)


def mk(code, entry_i, entry=100.0, stop=90.0, conviction=0.5, events=((10, ps.EVENT_EXIT, 110.0, 1.0),),
       adv=1e12, lines=None, mrkt="J"):
    t = ps.UnitTrade(
        market=ps.market_of(mrkt), code=code, mrkt_code=mrkt, entry_date=D[entry_i], entry_price=entry,
        initial_stop=stop, conviction=conviction, adv=adv,
        events=[(D[i], kind, price, ratio) for i, kind, price, ratio in events],
        exit_lines={D[i]: v for i, v in (lines or {}).items()})
    return t


def run(trades, closes=None, **kw):
    closes = closes or {(t.mrkt_code, t.code): flat() for t in trades}
    return ps.simulate(trades, closes, ps.SimParams(equity0=kw.pop("equity0", 1_000_000), **kw), START, END)


class SimulateTest(unittest.TestCase):
    def test_pool_sizing_and_costs(self):
        """총자산 100만 × 1% ÷ 손절폭 10 = 1,000주. 비용은 매수 수수료, 매도 수수료·거래세"""
        res = run([mk("A", 1)])
        [c] = res.cycles
        self.assertEqual(c["qty"], 1000)
        cost = 100_000 * (1 + COMM)
        net = 110_000 * (1 - COMM - TAX)
        self.assertAlmostEqual(c["pnl"], net - cost)
        self.assertAlmostEqual(c["r_net"], (net - cost) / 10_000)
        self.assertAlmostEqual(res.curve["equity"].iloc[-1], 1_000_000 + net - cost)

    def test_position_cap_limits_qty(self):
        res = run([mk("A", 1)], max_position_pct=0.05)
        self.assertEqual(res.cycles[0]["qty"], 500)
        self.assertEqual(res.cycles[0]["binding"], "position_cap")
        self.assertEqual(ps.sim_metrics(res)["cap_binding"], 1)

    def test_max_positions_keeps_higher_conviction(self):
        res = run([mk("LOW", 1, conviction=0.5), mk("HIGH", 1, conviction=0.9)], max_positions=1)
        self.assertEqual([c["code"] for c in res.cycles], ["HIGH"])
        self.assertEqual(res.skipped["max_positions"], 1)
        self.assertEqual(res.signals, 2)

    def test_priority_narrow_stop(self):
        wide = mk("WIDE", 1, stop=90.0, conviction=0.9)     # 손절폭 10%, conviction 높음
        narrow = mk("NARROW", 1, stop=95.0, conviction=0.5)  # 손절폭 5%
        self.assertEqual(run([wide, narrow], max_positions=1, priority="conviction").cycles[0]["code"], "WIDE")
        self.assertEqual(run([wide, narrow], max_positions=1, priority="narrow_stop").cycles[0]["code"], "NARROW")

    def test_random_priority_is_seeded(self):
        trades = lambda: [mk("A", 1), mk("B", 1)]
        pick = lambda seed: run(trades(), max_positions=1, priority="random", seed=seed).cycles[0]["code"]
        self.assertEqual(pick(3), pick(3))
        self.assertEqual({pick(s) for s in range(20)}, {"A", "B"})   # 시드에 따라 둘 다 뽑힐 수 있다

    def test_exits_first_frees_cash(self):
        """같은 날 A 청산 + B 진입 — 청산 먼저면 그 대금으로 B를 산다, 반대면 현금 부족으로 건너뛴다"""
        a = mk("A", 1, stop=99.0, events=((5, ps.EVENT_EXIT, 100.0, 1.0),))
        b = mk("B", 5, stop=99.0, events=((10, ps.EVENT_EXIT, 100.0, 1.0),))
        first = run([a, b], max_position_pct=0.9, exits_first=True)
        self.assertEqual({c["code"] for c in first.cycles}, {"A", "B"})
        later = run([mk("A", 1, stop=99.0, events=((5, ps.EVENT_EXIT, 100.0, 1.0),)),
                     mk("B", 5, stop=99.0, events=((10, ps.EVENT_EXIT, 100.0, 1.0),))],
                    max_position_pct=0.9, exits_first=False)
        self.assertEqual({c["code"] for c in later.cycles}, {"A"})
        self.assertEqual(later.skipped["cash"], 1)

    def test_open_risk_limit_and_release_at_breakeven(self):
        """총 열린 리스크 1.5% 한도 — 1%짜리 둘은 못 담지만, 첫 포지션 청산선이 본전 이상이면 담는다"""
        a = lambda lines: mk("A", 1, lines=lines)
        b = lambda: mk("B", 3)
        blocked = run([a(None), b()], max_open_risk_pct=0.015)
        self.assertEqual([c["code"] for c in blocked.cycles], ["A"])
        self.assertEqual(blocked.skipped["open_risk"], 1)
        released = run([a({2: 100.0, 3: 100.0}), b()], max_open_risk_pct=0.015)
        self.assertEqual({c["code"] for c in released.cycles}, {"A", "B"})

    def test_liquidity_blocks_including_unknown_adv(self):
        self.assertEqual(run([mk("A", 1, adv=1000.0)]).skipped["liquidity"], 1)
        self.assertEqual(run([mk("A", 1, adv=float("nan"))]).skipped["liquidity"], 1)

    def test_zero_qty_when_no_stop_width(self):
        res = run([mk("A", 1, stop=100.0)])
        self.assertEqual(res.skipped["zero_qty"], 1)
        self.assertEqual(res.cycles, [])

    def test_partial_then_exit_pnl(self):
        events = ((5, ps.EVENT_PARTIAL, 150.0, 0.5), (8, ps.EVENT_EXIT, 120.0, 0.5))
        [c] = run([mk("A", 1, events=events)]).cycles
        k = 1 - COMM - TAX
        self.assertAlmostEqual(c["pnl"], 500 * 150 * k + 500 * 120 * k - 100_000 * (1 + COMM))

    def test_us_market_costs_include_fx(self):
        """미국: 수수료 0.25% + 환전 0.25%를 매수·매도 각각, 거래세 없음. 통화는 달러 기준"""
        t = mk("AAPL", 1, mrkt="NAS")
        res = run([t], equity0=100_000)
        [c] = res.cycles
        self.assertEqual(c["qty"], 100)
        self.assertAlmostEqual(c["pnl"], 100 * 110 * (1 - 0.0025 - 0.0025) - 100 * 100 * (1 + 0.0025 + 0.0025))

    def test_final_liquidation_event_closes_position(self):
        res = run([mk("A", 30, events=((39, ps.EVENT_FINAL, 100.0, 1.0),))])
        self.assertEqual(len(res.cycles), 1)
        self.assertEqual(res.curve["n_pos"].iloc[-1], 0)

    def test_same_day_entry_and_final_liquidation(self):
        """구간 마지막 날 진입 + 최종 청산 — 청산 먼저 순서에서도 놓치지 않고 비용을 반영한다"""
        for exits_first in (True, False):
            res = run([mk("A", 39, events=((39, ps.EVENT_FINAL, 100.0, 1.0),))], exits_first=exits_first)
            [c] = res.cycles
            self.assertEqual(res.curve["n_pos"].iloc[-1], 0)
            self.assertAlmostEqual(c["pnl"], 100_000 * (1 - COMM - TAX) - 100_000 * (1 + COMM))

    def test_skipped_entry_exit_event_ignored(self):
        """건너뛴 진입의 청산 이벤트가 없는 포지션을 팔아 현금을 만들지 않는다"""
        res = run([mk("A", 1, adv=1.0)])
        self.assertEqual(res.curve["equity"].iloc[-1], 1_000_000)

    def test_park_interest_accrues_on_cash(self):
        dates = pd.bdate_range("2021-01-04", "2022-01-03")
        res = ps.simulate([], {("J", "X"): pd.Series(100.0, index=dates)},
                          ps.SimParams(equity0=1_000_000, park_apy=0.0365), START, "2022-12-31")
        days = (dates[-1] - dates[0]).days
        self.assertAlmostEqual(res.curve["equity"].iloc[-1], 1_000_000 * 1.0365 ** (days / 365), places=4)

    def test_invalid_mode_and_fixed_alloc_needs_n_stocks(self):
        with self.assertRaises(ValueError):
            ps.simulate([], {}, ps.SimParams(mode="x"), START, END)
        with self.assertRaises(ValueError):
            ps.simulate([mk("A", 1)], {("J", "A"): flat()}, ps.SimParams(mode="fixed_alloc"), START, END)


class FixedAllocTest(unittest.TestCase):
    """E0 — 현재 구조(종목별 배정금) 재현"""

    def run_fixed(self, trades, n=10, equity0=10_000_000):
        closes = {(t.mrkt_code, t.code): flat() for t in trades}
        return ps.simulate(trades, closes, ps.SimParams(mode="fixed_alloc", equity0=equity0, n_stocks=n), START, END)

    def test_qty_from_allocation_and_conviction(self):
        """배정금 100만 × 0.8 × conviction 0.5 ÷ 100 = 4,000주. 한도는 적용되지 않는다"""
        res = self.run_fixed([mk("A", 1, conviction=0.5), mk("B", 1, conviction=1.0)])
        qty = {c["code"]: c["qty"] for c in res.cycles}
        self.assertEqual(qty, {"A": 4000, "B": 8000})

    def test_idle_allocation_counts_as_cash(self):
        """신호가 없던 종목의 배정금도 총자산에 들어간다 — 노는 돈이 지표에서 사라지면 비교가 왜곡된다"""
        res = self.run_fixed([mk("A", 20)])
        self.assertEqual(res.curve["equity"].iloc[0], 10_000_000)

    def test_stock_allocation_compounds_separately(self):
        """종목 A의 이익은 A의 다음 진입 규모만 키운다 (다른 종목 배정금과 섞이지 않는다)"""
        first = mk("A", 1, conviction=0.5, events=((5, ps.EVENT_EXIT, 130.0, 1.0),))
        second = mk("A", 10, conviction=0.5, events=((15, ps.EVENT_EXIT, 130.0, 1.0),))
        other = mk("B", 10, conviction=0.5, events=((20, ps.EVENT_EXIT, 110.0, 1.0),))
        res = self.run_fixed([first, second, other])
        qty = sorted((c["code"], c["entry_date"], c["qty"]) for c in res.cycles)
        a_second = next(q for code, d, q in qty if code == "A" and d == D[10])
        b = next(q for code, d, q in qty if code == "B")
        self.assertGreater(a_second, 4000)
        self.assertEqual(b, 4000)


class JudgmentTest(unittest.TestCase):
    """설계 문서 6장에서 고정한 판정 기준 — 결과를 해석하는 부분이 틀리면 실험 전체가 의미 없다"""

    def test_criterion1_ratio(self):
        self.assertTrue(ps.passes_mar_ratio(1.2, 1.0))      # 정확히 1.2배
        self.assertFalse(ps.passes_mar_ratio(1.19, 1.0))
        self.assertFalse(ps.passes_mar_ratio(None, 1.0))    # MDD 0 등으로 MAR 없음

    def test_criterion1_when_e0_not_positive(self):
        """E0 MAR 가 0 이하면 1.2배가 오히려 쉬워진다 — 양수이면서 E0 보다 높아야 통과"""
        self.assertFalse(ps.passes_mar_ratio(-0.2, -0.5))   # 1.2배(-0.6) 기준이면 통과해버리는 경우
        self.assertFalse(ps.passes_mar_ratio(0.0, -0.5))
        self.assertTrue(ps.passes_mar_ratio(0.1, -0.5))
        self.assertTrue(ps.passes_mar_ratio(0.1, 0.0))
        self.assertTrue(ps.passes_mar_ratio(0.1, None))

    def test_control_threshold_is_90th_percentile(self):
        vals = [float(i) for i in range(1, 31)]             # 1..30
        self.assertAlmostEqual(ps.control_threshold(vals), 27.1)
        self.assertEqual(ps.control_threshold([]), None)

    def test_control_threshold_treats_none_as_zero(self):
        self.assertEqual(ps.control_threshold([None] * 30), 0.0)

    def test_grid_size(self):
        self.assertEqual(len(ps.grid_configs(ps.GRID)), 4 * 4 * 3 * 3)

    def test_neighbors_move_one_axis_one_step(self):
        grid = {"a": [1, 2, 3], "b": ["x", "y"]}
        self.assertEqual(sorted(ps.grid_neighbors((2, "x"), grid), key=str),
                         sorted([(1, "x"), (3, "x"), (2, "y")], key=str))

    def test_neighbors_stay_inside_grid(self):
        grid = {"a": [1, 2, 3], "b": ["x", "y"]}
        self.assertEqual(sorted(ps.grid_neighbors((1, "x"), grid), key=str), sorted([(2, "x"), (1, "y")], key=str))

    def test_neighbors_handle_none_axis_value(self):
        """max_positions 의 None(제한 없음)도 축의 한 칸이다"""
        grid = {"n": [10, 20, None]}
        self.assertEqual(ps.grid_neighbors((None,), grid), [(20,)])

    def test_cfg_kwargs_maps_axes(self):
        cfg = ps.grid_configs(ps.GRID)[0]
        self.assertEqual(set(ps.cfg_kwargs(cfg, ps.GRID)), set(ps.GRID))

    def test_holdout_runs_once(self):
        """홀드아웃은 한 번만 — 표시 파일이 있으면 데이터를 읽기 전에 중단한다"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / ".holdout_done"
            marker.write_text("done")
            with patch.object(ps, "HOLDOUT_MARKER", marker), patch.object(ps, "prepare") as prep:
                with self.assertRaises(SystemExit):
                    ps.holdout({"risk_pct": 0.01}, force=False)
                prep.assert_not_called()


class MetricsTest(unittest.TestCase):
    def test_loss_trade_drawdown_and_r(self):
        """종가가 손절폭(10)만큼 빠져 청산 — 낙폭 약 1%, 순 R은 비용만큼 −1보다 조금 나쁘다"""
        close = pd.Series([100.0] * 5 + [90.0] * 35, index=D)
        t = mk("A", 1, events=((5, ps.EVENT_EXIT, 90.0, 1.0),))
        res = ps.simulate([t], {("J", "A"): close}, ps.SimParams(equity0=1_000_000), START, END)
        m = ps.sim_metrics(res)
        self.assertGreater(m["mdd"], 0.009)
        self.assertLess(m["mdd"], 0.013)
        self.assertLess(m["mean_r_net"], -1.0)
        self.assertGreater(m["mean_r_net"], -1.1)
        self.assertEqual(m["trades"], 1)
        self.assertEqual(m["win_rate"], 0.0)

    def test_exposure_and_cash_ratio(self):
        res = run([mk("A", 1, events=((10, ps.EVENT_EXIT, 100.0, 1.0),))])
        m = ps.sim_metrics(res)
        self.assertAlmostEqual(m["exposure"] + m["cash_ratio"], 1.0, places=3)
        self.assertGreater(m["exposure"], 0.0)
        self.assertEqual(m["max_positions"], 1)


if __name__ == "__main__":
    unittest.main()
