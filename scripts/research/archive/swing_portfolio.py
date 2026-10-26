"""오더블럭 v3 검증 — 스윙 합산 시뮬레이터 · 비용/체결 강화 · 품질 점수 · 실험 E1~E6 (연구용)

계획: docs/01-plan/features/order-block-v3.plan.md

실행 예:
  uv run python scripts/research/swing_portfolio.py dev                 # 개발 구간 E1~E6
  uv run python scripts/research/swing_portfolio.py holdout --config '{...}'   # 홀드아웃 1회 (최종 후보만)

구간 (계획서 4.2, 결과 보기 전 고정):
  개발     2020-05-01 ~ 2026-10-23
  홀드아웃 2015-01-01 ~ 2019-12-31  (--holdout 실행은 최종 후보로 단 1회)

스윙 구조 (계획서 3.2): 종목마다 같은 배정금(ALLOCATION)의 스윙을 하나씩 두고, 시장별로 일별 평가금을 합산한다.
"""
import argparse
import asyncio
import json
import math
import random
import statistics as st
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import order_block_research as obr  # noqa: E402
from app.core.market_code import is_overseas  # noqa: E402
from app.domain.swing.backtest.strategies import base_strategy  # noqa: E402
from app.domain.swing.backtest.strategies.base_strategy import fill_buy  # noqa: E402
from app.domain.swing.backtest.strategies.order_block_backtest_strategy import OrderBlockBacktestStrategy  # noqa: E402
from app.domain.swing.order_block import active_order_block, detect_order_blocks  # noqa: E402

PERIODS = {"dev": ("2020-05-01", "2026-10-23"), "holdout": ("2015-01-01", "2019-12-31")}
ALLOCATION = 10_000_000           # 스윙 1개 배정금 (시장별 통화 단위)
FX_COST = 0.0025                  # 미국 환전 비용 (매수·매도 각각, 원화 투자 기준)
SLIPPAGE_TICKS_V3 = 2             # 계획서 3.5 — 모든 체결 2틱 (시가·갭일만 2틱보다 보수적)
MIN_WARMUP_BARS = 130             # 구간 시작 전 이만큼 일봉이 없으면 그 구간에서 제외 (EMA120 워밍업 121봉 + 여유)


# ==================== 전략 (v3 체결 모델 + 품질 점수 문턱) ====================

class V3Strategy(OrderBlockBacktestStrategy):
    """v2.1 + 계획서 3.5 진입 체결 모델 + 3.3 품질 점수 문턱(옵션)

    진입가 = max(근접선, 당일 저가 + ENTRY_DELAY_ATR × ATR) — 실전은 존 터치 후 10분 연속 확인 뒤 사므로
    그 사이 반등분을 내고 산다고 본다. 당일 고가를 넘지는 않는다.
    """
    ENTRY_DELAY_ATR = 0.1
    FULL_ENTRY = False                # True면 신호 시 배정금 99% 매수 (서비스 FULL_ENTRY_YN='Y'와 같은 규칙)
    QUALITY_MIN = 0.0                 # 품질 점수 문턱 (0이면 전부 매수)
    QUALITY: Dict[Tuple, float] = {}  # (종목, ob_date) → 점수 — 실행 전에 채운다

    def _find_entry(self, ctx, i, row, prev, atr, used, stats):
        found = super()._find_entry(ctx, i, row, prev, atr, used, stats)
        if not found:
            return None
        ob = active_order_block(ctx["df"], ctx["candidates"], i - 1, ctx["ob_params"])
        if self.QUALITY_MIN > 0:
            score = self.QUALITY.get((self._code, ob.ob_date))
            if score is not None and score < self.QUALITY_MIN:
                stats["blocked"]["quality"] = stats["blocked"].get("quality", 0) + 1
                return None
        if not found.get("rebound"):
            raw = max(ob.proximal, float(row["STCK_LWPR"]) + self.ENTRY_DELAY_ATR * atr)
            found["price"] = fill_buy(min(raw, float(row["STCK_HGPR"])), ctx["mrkt_code"])
        return found

    def compute(self, prices_df, params):
        self._code = params.get("st_code")     # 품질 점수 조회용 (인스턴스는 종목마다 새로 만든다)
        return super().compute(prices_df, {**params, "full_entry": self.FULL_ENTRY or params.get("full_entry", False)})


class V3Random(V3Strategy):
    """무작위 진입 대조군 — 청산·쿨다운·비용은 V3와 같다 (계획서 4.4 E6)"""
    P = 0.01
    K = 1.5
    SEED = 0

    def __init__(self):
        super().__init__()
        self.rng = random.Random(self.SEED)

    def _find_entry(self, ctx, i, row, prev, atr, used, stats):
        if atr <= 0 or self.rng.random() >= self.P:
            return None
        price = fill_buy(float(row["STCK_OPRC"]), ctx["mrkt_code"])
        return {"price": price, "stop": price - self.K * atr, "reasons": ["무작위"], "key": ("rnd", i)}


# ==================== 품질 점수 (계획서 3.3) ====================

def quality_scores(data: Dict[tuple, pd.DataFrame], index_close: Dict[str, pd.Series],
                   since: Optional[str] = None) -> Dict[Tuple, float]:
    """OB마다 품질 점수 — 그 OB의 BOS일 이전에 형성된 같은 시장 OB들과 비교한 백분위 평균 (선견 없음)

    요소: 변위/ATR(↑) · 임펄스 거래량/20일 평균(↑) · 존 폭/ATR(↓) · 60일 상대 강도(종목 − 지수, ↑)
    과거 OB가 50개 미만이면 점수를 매기지 않는다 (None → 문턱 적용 안 함).
    since: 이 날짜 이후 BOS만 비교 대상으로 (종목마다 과거 데이터 길이가 달라도 같은 조건이 되도록).
    """
    s = OrderBlockBacktestStrategy()
    rows = []
    for (m, c), raw in data.items():
        df = s._calculate_indicators(s._prepare_data(raw.copy())).reset_index(drop=True)
        mk = "KR" if m == "J" else "US"
        idx = index_close.get(mk)
        close = df["STCK_CLPR"].to_numpy(float)
        for ob in detect_order_blocks(df, s.ob_params()):
            k, j = ob.bos_idx, ob.ob_idx
            atr = float(df["atr"].iloc[k])
            vol = float(df["ACML_VOL"].iloc[j + 1:k + 1].astype(float).max()) / float(df["avg_vol20"].iloc[k])
            rs = None
            if k >= 60 and idx is not None:
                d0, d1 = df["STCK_BSOP_DATE"].iloc[k - 60], df["STCK_BSOP_DATE"].iloc[k]
                i0, i1 = idx.asof(d0), idx.asof(d1)
                if i0 and i1 and not math.isnan(i0) and not math.isnan(i1):
                    rs = (close[k] / close[k - 60] - 1) - (i1 / i0 - 1)
            rows.append({"key": (c, ob.ob_date), "mk": mk, "date": df["STCK_BSOP_DATE"].iloc[k],
                         "disp": (close[k] - ob.distal) / atr, "vol": vol,
                         "zone": (float(df["STCK_HGPR"].iloc[j]) - ob.distal) / atr, "rs": rs})
    scores = {}
    if since:
        rows = [r for r in rows if r["date"] >= pd.Timestamp(since)]
    for mk in ("KR", "US"):
        obs = sorted((r for r in rows if r["mk"] == mk), key=lambda r: r["date"])
        for n, r in enumerate(obs):
            past = obs[:n]
            if len(past) < 50:
                continue
            pct = []
            for f, higher_better in (("disp", True), ("vol", True), ("zone", False), ("rs", True)):
                vals = [p[f] for p in past if p[f] is not None]
                if r[f] is None or not vals:
                    continue
                below = sum(1 for v in vals if v < r[f]) / len(vals)
                pct.append(below if higher_better else 1 - below)
            if pct:
                scores[r["key"]] = sum(pct) / len(pct)
    return scores


# ==================== 스윙 합산 곡선 ====================

def equity_curve(df: pd.DataFrame, trades: List[Dict], start: str, mrkt_code: str) -> pd.DataFrame:
    """스윙 1개의 일별 평가금 (현금 + 보유 수량 × 종가) — 미국은 환전 비용을 체결마다 뺀다"""
    dates = pd.to_datetime(df["STCK_BSOP_DATE"].astype(str))
    close = df["STCK_CLPR"].astype(float).to_numpy()
    by_date: Dict[pd.Timestamp, List[Dict]] = {}
    for t in trades:
        by_date.setdefault(pd.Timestamp(t["date"]), []).append(t)
    fx = FX_COST if is_overseas(mrkt_code) else 0.0
    cash, qty, out = float(ALLOCATION), 0, []
    start_ts = pd.Timestamp(start)
    for d, c in zip(dates, close):
        if d < start_ts:
            continue
        for t in by_date.get(d, []):
            amount = float(t["amount"])
            if t["action"] == "BUY":
                cash -= amount + float(t.get("commission", 0)) + amount * fx
                qty += int(t["quantity"])
            else:
                cash += float(t.get("net_proceeds", amount)) - amount * fx
                qty -= int(t["quantity"])
        out.append((d, cash + qty * c, qty * c))
    return pd.DataFrame(out, columns=["date", "equity", "invested"]).set_index("date")


def portfolio_metrics(curves: List[pd.DataFrame], cycles: List[Dict], start: str, end: str) -> Dict:
    """시장별 합산 곡선 지표 (계획서 4.3)"""
    if not curves:
        return {"swings": 0}
    eq = pd.concat([c["equity"] for c in curves], axis=1).sort_index().ffill()
    inv = pd.concat([c["invested"] for c in curves], axis=1).sort_index().ffill().fillna(0)
    # 늦게 편입된 스윙은 편입 전 배정금을 현금으로 본다
    eq = eq.fillna(ALLOCATION)
    total, invested = eq.sum(axis=1), inv.sum(axis=1)
    ret = total.pct_change().dropna()
    days = (total.index[-1] - total.index[0]).days or 1
    cagr = (total.iloc[-1] / total.iloc[0]) ** (365.25 / days) - 1
    peak = total.cummax()
    mdd = float(((peak - total) / peak).max())
    downside = ret[ret < 0].std()
    monthly = total.resample("ME").last().pct_change().dropna()
    streak = longest = 0
    for c in sorted(cycles, key=lambda c: c["exit_date"]):
        streak = streak + 1 if c["r_net"] < 0 else 0
        longest = max(longest, streak)
    r = [c["r_net"] for c in cycles]
    return {
        "swings": len(curves), "cagr": round(cagr, 4), "mdd": round(mdd, 4),
        "mar": round(cagr / mdd, 2) if mdd > 0 else None,
        "sharpe": round(ret.mean() / ret.std() * math.sqrt(252), 2) if ret.std() > 0 else None,
        "sortino": round(ret.mean() / downside * math.sqrt(252), 2) if downside and downside > 0 else None,
        "monthly_win": round(float((monthly > 0).mean()), 3) if len(monthly) else None,
        "exposure": round(float((invested / total).mean()), 3),
        "trades": len(r), "per_year": round(len(r) / (days / 365.25), 1),
        "mean_r": round(st.mean(r), 3) if r else None,
        "t": round(st.mean(r) / (st.stdev(r) / math.sqrt(len(r))), 2) if len(r) > 1 and st.stdev(r) > 0 else None,
        "win_rate": round(sum(1 for x in r if x > 0) / len(r), 3) if r else None,
        "longest_losing_streak": longest,
    }


def buy_and_hold(data: Dict[tuple, pd.DataFrame], start: str, end: str) -> Dict:
    """같은 종목들을 구간 시작일에 배정금만큼 사서 보유 — 시장별 합산 (비용 1회 반영)"""
    out = {}
    for mk in ("KR", "US"):
        curves = []
        for (m, c), df in data.items():
            if ("KR" if m == "J" else "US") != mk:
                continue
            sub = df[(df["STCK_BSOP_DATE"] <= end.replace("-", ""))]
            if (sub["STCK_BSOP_DATE"] < start.replace("-", "")).sum() < MIN_WARMUP_BARS:
                continue
            sub = sub[sub["STCK_BSOP_DATE"] >= start.replace("-", "")]
            close = sub["STCK_CLPR"].astype(float).to_numpy()
            cost = 0.00147 if mk == "KR" else 0.0025 + FX_COST
            qty = int(ALLOCATION * (1 - cost) // close[0])
            cash = ALLOCATION - qty * close[0] * (1 + cost)
            idx = pd.to_datetime(sub["STCK_BSOP_DATE"].astype(str))
            curves.append(pd.DataFrame({"equity": cash + qty * close, "invested": qty * close}, index=idx))
        out[mk] = portfolio_metrics(curves, [], start, end)
    return out


def benchmark(index_df: Optional[pd.DataFrame], start: str, end: str) -> Dict:
    """지수 ETF 단순 보유"""
    if index_df is None:
        return {}
    d = index_df.copy()
    d["date"] = pd.to_datetime(d["STCK_BSOP_DATE"].astype(str))
    d = d[(d["date"] >= start) & (d["date"] <= end)].set_index("date")["STCK_CLPR"].astype(float)
    if len(d) < 2:
        return {}
    days = (d.index[-1] - d.index[0]).days
    cagr = (d.iloc[-1] / d.iloc[0]) ** (365.25 / days) - 1
    mdd = float(((d.cummax() - d) / d.cummax()).max())
    return {"cagr": round(cagr, 4), "mdd": round(mdd, 4), "mar": round(cagr / mdd, 2) if mdd else None}


# ==================== 실행 ====================

_SHARED: Dict = {}


def run_config(strategy_cls, period: str) -> Dict:
    """설정 하나를 구간 하나에 — 시장별 합산 지표"""
    start, end = PERIODS[period]
    data = _SHARED["data"]
    by_market = {"KR": ([], []), "US": ([], [])}
    for (m, c), df in data.items():
        sub = df[df["STCK_BSOP_DATE"] <= end.replace("-", "")]
        if (sub["STCK_BSOP_DATE"] < start.replace("-", "")).sum() < MIN_WARMUP_BARS:
            continue   # 구간 시작 전 워밍업 부족 (그 뒤 상장 등)
        result = strategy_cls().compute(sub.copy(), {
            "st_code": c, "mrkt_code": m, "swing_type": "O", "init_amount": ALLOCATION,
            "eval_start": start, "market_regime": _SHARED["regimes"].get("KR" if m == "J" else "US"),
        })
        mk = "KR" if m == "J" else "US"
        cycles = []
        for cy in result.get("strategy_stats", {}).get("cycles", []):   # 단일 이평선 등은 사이클 기록 없음
            cy = dict(cy)
            if mk == "US" and cy.get("r"):   # 환전 비용(매수·매도 각 FX_COST)을 R 단위로 환산해 뺀다
                cy["r_net"] -= 2 * FX_COST * cy["entry"] / cy["r"]
            cycles.append(cy)
        by_market[mk][0].append(equity_curve(sub, result["trades"], start, m))
        by_market[mk][1].extend(cycles)
    return {mk: portfolio_metrics(curves, cycles, start, end) for mk, (curves, cycles) in by_market.items()}


def _task(args):
    label, overrides, period = args
    cls = type("C", (V3Strategy,), dict(overrides))
    return label, period, run_config(cls, period)


def _random_task(args):
    period, p, k, seed, overrides = args
    cls = type("R", (V3Random,), {"P": p, "K": k, "SEED": seed, **overrides})
    return run_config(cls, period)


def prepare(holdout_ok: bool = False):
    base_strategy.SLIPPAGE_TICKS = SLIPPAGE_TICKS_V3          # 계획서 3.5 (이 프로세스에서만)
    universe = asyncio.run(obr.load_universe(since="2013-01-01"))
    regimes = obr.market_regimes(universe)
    index_close = {}
    for mk, key in obr.MARKET_INDEX.items():
        if key in universe:
            d = universe[key]
            index_close[mk] = pd.Series(d["STCK_CLPR"].astype(float).to_numpy(),
                                        index=pd.to_datetime(d["STCK_BSOP_DATE"].astype(str)))
    data = obr.tradable(universe)
    _SHARED.update(data=data, regimes=regimes, universe=universe)
    return data, regimes, index_close


# ==================== 실험 (계획서 4.4) ====================

EXPERIMENTS = {
    "E1 기준선(v2.1+v3 체결·비용)": {},
    "E2 스윙 위험 1%": {"RISK_PER_TRADE": 0.01},
    "E2 스윙 위험 3%": {"RISK_PER_TRADE": 0.03},
    "E3 품질 상위 절반만": {"QUALITY_MIN": 0.5},
    "E4 쿨다운 없음": {"COOLDOWN_MODE": "none"},
    "E4 쿨다운 10거래일": {"COOLDOWN_MODE": "days", "COOLDOWN_DAYS": 10},
    "E5 시장 국면 필터": {"MARKET_FILTER": True},
}


def random_runs(period: str, overrides: Dict, runs: int, target: Dict[str, int]) -> Dict[str, List[Dict]]:
    """같은 규칙의 무작위 진입 runs회 — 시장별 지표 목록 (진입 수를 기준선과 비슷하게 p 보정)"""
    k = 1.0 + OrderBlockBacktestStrategy.STOP_ATR_BUFFER     # 손절폭 ≈ 존 폭(약 1 ATR) + 버퍼
    probe = run_config(type("R", (V3Random,), {"P": 0.01, "K": k, "SEED": 10_000, **overrides}), period)
    n_probe = sum(probe[mk].get("trades", 0) for mk in probe) or 1
    p = min(0.5, 0.01 * sum(target.values()) / n_probe)
    with obr._pool() as pool:
        results = pool.map(_random_task, [(period, p, k, seed, overrides) for seed in range(runs)])
    return {mk: [r[mk] for r in results] for mk in ("KR", "US")}


def dev(runs: int, out: str):
    data, regimes, index_close = prepare()
    # fork 전에 채워 작업 프로세스가 물려받는다. 개발 구간은 2019-06 이후 OB만 비교 (과거 적재 길이가 종목마다 달라서)
    V3Strategy.QUALITY = quality_scores(data, index_close, since="2019-06-01")
    print(f"종목 {len(data)} · 품질 점수 {len(V3Strategy.QUALITY)}개 OB", flush=True)
    obr._SHARED.update(data=data, regimes=regimes)

    results = {"benchmark": {mk: benchmark(_SHARED['universe'].get(key), *PERIODS["dev"])
                             for mk, key in obr.MARKET_INDEX.items()}}
    print("[벤치마크] " + json.dumps(results["benchmark"], ensure_ascii=False), flush=True)
    with obr._pool() as pool:
        for label, period, m in pool.imap_unordered(_task, [(l, ov, "dev") for l, ov in EXPERIMENTS.items()]):
            results[label] = m
    for label in EXPERIMENTS:
        print(f"[{label}] " + json.dumps(results[label], ensure_ascii=False), flush=True)

    base = results["E1 기준선(v2.1+v3 체결·비용)"]
    rnd = random_runs("dev", {}, runs, {mk: base[mk].get("trades", 0) for mk in base})
    summary = {}
    for mk in ("KR", "US"):
        mars = [r["mar"] for r in rnd[mk] if r.get("mar") is not None]
        summary[mk] = {"runs": len(mars), "mar_mean": round(st.mean(mars), 2) if mars else None,
                       "mar_std": round(st.stdev(mars), 2) if len(mars) > 1 else None,
                       "base_percentile": round(sum(1 for x in mars if x < (base[mk].get("mar") or -9)) / len(mars), 3)
                       if mars else None}
    results["E6 무작위 진입(기준선 규칙)"] = summary
    print("[E6 무작위 진입] " + json.dumps(summary, ensure_ascii=False), flush=True)

    # 채택 판정 — 두 시장 모두 MAR이 기준선 + 무작위 MAR 표준편차보다 높아야
    verdict = {}
    for label in EXPERIMENTS:
        if label.startswith("E1"):
            continue
        ok = all(results[label][mk].get("mar") is not None and base[mk].get("mar") is not None
                 and summary[mk]["mar_std"] is not None
                 and results[label][mk]["mar"] - base[mk]["mar"] > summary[mk]["mar_std"] for mk in ("KR", "US"))
        verdict[label] = "채택" if ok else "현행 유지"
    results["판정"] = verdict
    print("[판정] " + json.dumps(verdict, ensure_ascii=False), flush=True)
    if out:
        Path(out).write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str))


def holdout(config: Dict, runs: int, out: str):
    """최종 후보로 홀드아웃 1회 (계획서 6장 관문)"""
    data, regimes, index_close = prepare()
    V3Strategy.QUALITY = quality_scores(data, index_close)
    obr._SHARED.update(data=data, regimes=regimes)
    cls = type("F", (V3Strategy,), dict(config))
    res = run_config(cls, "holdout")
    bench = {mk: benchmark(_SHARED['universe'].get(key), *PERIODS["holdout"]) for mk, key in obr.MARKET_INDEX.items()}
    rnd = random_runs("holdout", config, runs, {mk: res[mk].get("trades", 0) for mk in res})
    pct = {mk: round(sum(1 for r in rnd[mk] if (r.get("mar") or -9) < (res[mk].get("mar") or -9)) / len(rnd[mk]), 3)
           for mk in rnd}
    print("[홀드아웃] " + json.dumps({"result": res, "benchmark": bench, "random_mar_percentile": pct},
                                    ensure_ascii=False), flush=True)
    if out:
        Path(out).write_text(json.dumps({"result": res, "benchmark": bench, "random_pct": pct},
                                        ensure_ascii=False, indent=2, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["dev", "holdout"])
    ap.add_argument("--runs", type=int, default=50)
    ap.add_argument("--config", default="{}", help="holdout 최종 후보 설정 (JSON)")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.mode == "dev":
        dev(args.runs, args.out)
    else:
        holdout(json.loads(args.config), args.runs, args.out)


if __name__ == "__main__":
    main()
