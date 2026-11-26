"""종목 간 상대 모멘텀 연구 (연구용 — 실전 코드와 무관)

사전 등록: scripts/research/PREREG_relative_momentum.md (결과를 보고 바꾸지 않는다)

월말 종가 기준 모멘텀(M1 12-1 / M2 6-1) 상위 10종목을 보유한다. 체결은 다음 거래일 시가, 계속 상위인 종목은 거래하지 않고
이탈 종목만 교체한다. 종목당 비중은 풀의 종목당 최대 비중(9.5%). 대조군은 종목 라벨 셔플(다른 종목의 점수로 고르기).

실행 (프로젝트 루트에서):
  PYTHONPATH=. uv run python scripts/research/relative_momentum.py dev
  PYTHONPATH=. uv run python scripts/research/relative_momentum.py holdout-check
  PYTHONPATH=. uv run python scripts/research/relative_momentum.py holdout --cand M1
"""
import argparse
import asyncio
import json
import multiprocessing as mp
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import portfolio_sim as ps  # noqa: E402
from regime_exposure import SIGNAL_INDEX, index_deposit_cagr_at_mdd, yearly  # noqa: E402
from app.domain.swing.backtest.strategies.base_strategy import fill_buy, fill_sell, sell_tick  # noqa: E402

HERE = Path(__file__).resolve().parent
DEV_OUT = HERE / "relative_momentum_dev.json"
HOLDOUT_MARKER = HERE / ".holdout_momentum_done"

CANDS = {"M1": 252, "M2": 126}      # 후보 → 비교 시점 (봉). 최근 SKIP_BARS 봉은 뺀다 (12-1, 6-1)
SKIP_BARS = 21
TOP_N = 10
MIN_UNIVERSE = 20                   # 점수가 있는 종목이 이보다 적으면 그 달은 보유 집단을 바꾸지 않는다
SHUFFLE_RUNS = 30
MIN_CYCLES = 60
TOP_EXCLUDE = 10
ARTIFICIAL_STOP = 0.5               # 사이징 전용 인위적 손절선 (진입가의 50%) — 종목당 최대 비중이 항상 먼저 걸리게 한다
SIM_KW = {"mode": "pool", "risk_pct": 0.25, "max_position_pct": 0.095, "max_positions": TOP_N, "priority": "narrow_stop"}

_PANELS: Dict[str, Dict] = {}
_CLOSES: Dict[str, Dict[tuple, pd.Series]] = {}
_WINDOW = {"range": ps.PERIODS["dev"]}


# ==================== 패널 / 점수 ====================

def make_panel(data: Dict[tuple, pd.DataFrame], keys: List[tuple], end: str) -> Dict:
    """시장 하나의 종목들 → 날짜 × 종목 패널(종가·시가·20일 평균 거래대금). 열 이름은 'mrkt|code'"""
    close, open_, adv = {}, {}, {}
    for k in keys:
        df = data[k]
        df = df[df["STCK_BSOP_DATE"] <= end.replace("-", "")]
        idx = pd.to_datetime(df["STCK_BSOP_DATE"].astype(str))
        name = f"{k[0]}|{k[1]}"
        close[name] = pd.Series(df["STCK_CLPR"].astype(float).to_numpy(), index=idx)
        open_[name] = pd.Series(df["STCK_OPRC"].astype(float).to_numpy(), index=idx)
        adv[name] = ps.avg_daily_amount_before(df)
    c = pd.DataFrame(close).sort_index()
    panel = {"close": c, "open": pd.DataFrame(open_).reindex(c.index), "adv": pd.DataFrame(adv).reindex(c.index)}
    panel["score"] = {cand: c.shift(SKIP_BARS) / c.shift(look) - 1 for cand, look in CANDS.items()}
    month = c.index.year * 100 + c.index.month
    panel["reb"] = np.flatnonzero(np.r_[month[1:] != month[:-1], False])      # 월의 마지막 거래일 (다음 봉이 있어야 체결)
    return panel


def build_trades(panel: Dict, cand: str, start: str, end: str, perm: Optional[np.ndarray] = None) -> List[ps.UnitTrade]:
    """월말 순위 → 상위 TOP_N 보유 집단 → 이탈 종목만 교체. 신호는 t 종가, 체결은 t+1 시가. perm 이 있으면 라벨 셔플 대조군"""
    close, open_, adv = panel["close"], panel["open"], panel["adv"]
    idx = close.index
    score = panel["score"][cand]
    if perm is not None:                       # 종목 i 에 종목 perm[i] 의 점수를 붙인다 (모멘텀과 자기 미래 수익의 관계만 끊긴다)
        score = pd.DataFrame(score.to_numpy()[:, perm], index=score.index, columns=score.columns)
    s64, e64 = pd.Timestamp(start), pd.Timestamp(end)
    last_i = int(idx.searchsorted(e64, side="right")) - 1
    cols = list(close.columns)
    held: Dict[str, ps.UnitTrade] = {}
    trades: List[ps.UnitTrade] = []

    for r in panel["reb"]:
        t_next = idx[r + 1]
        if t_next < s64 or t_next > e64:
            continue
        s = score.iloc[r].dropna()
        if len(s) < MIN_UNIVERSE:
            continue
        target = set(s.nlargest(TOP_N).index)
        for col in list(held):                 # 이탈 종목만 판다 (다음 봉 시가가 없으면 다음 달로 미룬다)
            if col in target:
                continue
            o = open_[col].iloc[r + 1]
            if np.isnan(o):
                continue
            mrkt = col.split("|")[0]
            held[col].events.append((t_next, ps.EVENT_EXIT, fill_sell(float(o), mrkt), 1.0))
            trades.append(held.pop(col))
        for col in sorted(target - set(held)):  # 새로 상위에 든 종목을 산다
            o = open_[col].iloc[r + 1]
            if np.isnan(o):
                continue
            mrkt, code = col.split("|")
            px = fill_buy(float(o), mrkt)
            a = adv[col].iloc[r + 1]
            held[col] = ps.UnitTrade(
                market=ps.market_of(mrkt), code=code, mrkt_code=mrkt, entry_date=t_next, entry_price=px,
                initial_stop=sell_tick(px * ARTIFICIAL_STOP, mrkt), conviction=1.0,
                adv=float(a) if not np.isnan(a) else float("nan"))
    for col, t in held.items():                # 구간 마지막 날 종가로 최종 평가
        c = close[col].iloc[:last_i + 1].dropna()
        if c.empty or t.entry_date > idx[last_i]:
            continue
        t.events.append((idx[last_i], ps.EVENT_FINAL, fill_sell(float(c.iloc[-1]), col.split("|")[0]), 1.0))
        trades.append(t)
    return trades


# ==================== 실행 틀 ====================

def prepare(period: str) -> Dict[str, pd.Series]:
    universe = asyncio.run(ps.load_universe())
    data = ps.tradable(universe)
    start, end = ps.PERIODS[period]
    _WINDOW["range"] = (start, end)
    closes = ps.closes_by_stock(data)
    for mk in ("KR", "US"):
        keys = [k for k in data if ps.market_of(k[0]) == mk]
        _PANELS[mk] = make_panel(data, keys, end)
        _CLOSES[mk] = {k: closes[k] for k in keys}
    return {mk: ps.closes_by_stock({k: universe[k]})[k] for mk, k in SIGNAL_INDEX.items()}


def run_one(mk: str, cand: str, seed: Optional[int]) -> Optional[Dict]:
    start, end = _WINDOW["range"]
    panel = _PANELS[mk]
    perm = None if seed is None else np.random.default_rng(seed).permutation(panel["close"].shape[1])
    ts = build_trades(panel, cand, start, end, perm)
    if not ts:
        return None
    p = ps.SimParams(equity0=ps.EQUITY0[mk], park_apy=ps.PARK_APY[mk], **SIM_KW)

    def play(trades):
        keys = {(t.mrkt_code, t.code) for t in trades}
        return ps.simulate(trades, {k: v for k, v in _CLOSES[mk].items() if k in keys}, p, start, end)

    res = play(ts)
    m = ps.sim_metrics(res)
    if m.get("days") == 0:
        return None
    m["yearly"] = yearly(res.curve["equity"], p.equity0)
    if seed is None:
        m["cycles"] = len(res.cycles)
        drop = {(x["code"], x["entry_date"]) for x in sorted(res.cycles, key=lambda x: -x["pnl"])[:TOP_EXCLUDE]}
        rest = [t for t in ts if (t.code, t.entry_date) not in drop]
        m["mar_ex_top"] = ps.sim_metrics(play(rest)).get("mar") if rest else None
    return m


def _job(args):
    mk, cand, seed = args
    return mk, cand, seed, run_one(mk, cand, seed)


def run_jobs(jobs: List[tuple]) -> Dict[tuple, Optional[Dict]]:
    with mp.get_context("fork").Pool() as pool:
        return {(mk, cand, seed): m for mk, cand, seed, m in pool.map(_job, jobs, chunksize=2)}


def fmt(m: Optional[Dict]) -> str:
    if m is None:
        return "거래 없음"
    return (f"CAGR {m['cagr']:+.1%} · MDD {m['mdd']:.1%} · MAR {m['mar']} · 노출 {m['exposure']:.0%}"
            f" · 사이클 {m['trades']} · 승률 {m['win_rate']}")


def judge(res: Dict[tuple, Optional[Dict]], idx_close: Dict[str, pd.Series], mk: str, cand: str) -> Dict:
    m = res[(mk, cand, None)]
    if m is None:
        return {"m": None, "ok": False}
    start, end = _WINDOW["range"]
    ref = index_deposit_cagr_at_mdd(idx_close[mk], ps.PARK_APY[mk], start, end, m["mdd"])
    ctrl = [(res[(mk, cand, s)] or {}).get("mar") for s in range(SHUFFLE_RUNS)]
    thr = ps.control_threshold(ctrl)
    out = {"m": m, "ref": ref, "thr": thr, "ctrl": ctrl,
           "p1": ref is not None and m["cagr"] >= ref,
           "p2": thr is not None and (m["mar"] or 0) >= thr,
           "p3": m.get("mar_ex_top") is not None and m["mar_ex_top"] > 0,
           "sample": m["cycles"] >= MIN_CYCLES}
    out["ok"] = all(out[k] for k in ("p1", "p2", "p3", "sample"))
    return out


def show(j: Dict, mk: str) -> None:
    if j["m"] is None:
        print(f"[{mk}] 거래 없음 → 불합격")
        return
    ctrl = [c if c is not None else 0.0 for c in j["ctrl"]]
    print(f"[{mk}] {fmt(j['m'])}\n      연도별 {j['m']['yearly']}"
          f"\n      같은 MDD 지수+예금 CAGR {j['ref']:+.1%} · 셔플 대조군 MAR 중앙값 {np.median(ctrl):.2f}"
          f" · 90분위 {j['thr']:.2f} · 상위 제외 MAR {j['m'].get('mar_ex_top')}"
          f"\n      P1 {'O' if j['p1'] else 'X'}  P2 {'O' if j['p2'] else 'X'}  P3 {'O' if j['p3'] else 'X'}"
          f"  표본(≥{MIN_CYCLES}) {'O' if j['sample'] else 'X'}")


# ==================== dev / holdout ====================

def dev() -> None:
    idx_close = prepare("dev")
    start, end = _WINDOW["range"]
    for mk in ("KR", "US"):
        P = _PANELS[mk]
        valid = [int(P["score"]["M1"].iloc[r].notna().sum()) for r in P["reb"]
                 if start <= str(P["close"].index[r + 1].date()) <= end]
        print(f"[{mk}] 전체 {P['close'].shape[1]}종목 · 구간 내 리밸런싱 {len(valid)}회 · M1 점수 보유 종목 수 {min(valid)}~{max(valid)}")
    jobs = [(mk, cand, seed) for mk in ("KR", "US") for cand in CANDS for seed in [None, *range(SHUFFLE_RUNS)]]
    print(f"개발 구간 {start} ~ {end} · 작업 {len(jobs)}개 실행")
    res = run_jobs(jobs)
    passed = []
    for cand in CANDS:
        print(f"\n=== {cand} ({'12-1' if cand == 'M1' else '6-1'} 모멘텀, 상위 {TOP_N}) ===")
        ok_all = True
        for mk in ("KR", "US"):
            j = judge(res, idx_close, mk, cand)
            show(j, mk)
            ok_all = ok_all and j["ok"]
        if ok_all:
            passed.append(cand)
    print("\n=== 판정 ===")
    print("합격:", passed if passed else "없음")
    best = max(passed, key=lambda c: min(res[(mk, c, None)]["mar"] or -9 for mk in ("KR", "US"))) if passed else None
    if best:
        print(f"홀드아웃 후보(최고 min-MAR): {best} → holdout-check 후 holdout --cand {best}")
    DEV_OUT.write_text(json.dumps({
        "at": datetime.now().isoformat(timespec="seconds"), "passed": passed, "best": best,
        "results": {f"{mk}/{cand}": res[(mk, cand, None)] for mk in ("KR", "US") for cand in CANDS},
        "ctrl": {f"{mk}/{cand}": [(res[(mk, cand, s)] or {}).get("mar") for s in range(SHUFFLE_RUNS)]
                 for mk in ("KR", "US") for cand in CANDS}}, ensure_ascii=False, indent=1, default=str))
    print(f"저장: {DEV_OUT}")


def holdout_check() -> None:
    """홀드아웃 유효 종목 수만 점검 — 성과 계산 없음"""
    prepare("holdout")
    start, end = _WINDOW["range"]
    print(f"홀드아웃 {start} ~ {end} 점검 (성과 계산 없음)")
    for mk in ("KR", "US"):
        P = _PANELS[mk]
        for cand in CANDS:
            valid = [int(P["score"][cand].iloc[r].notna().sum()) for r in P["reb"]
                     if start <= str(P["close"].index[r + 1].date()) <= end]
            ok = [v for v in valid if v >= MIN_UNIVERSE]
            print(f"[{mk}] {cand}: 전체 {P['close'].shape[1]}종목 · 리밸런싱 {len(valid)}회 · 점수 보유 {min(valid)}~{max(valid)}종목"
                  f" · 유효(≥{MIN_UNIVERSE}) {len(ok)}회")


HOLDOUT_CAND = "M2"          # 2차 사전 등록(PREREG_relative_momentum_2.md) — 사후 가설, 이 후보 하나만 평가한다
MIN_VALID_REBALANCES = 36    # 판정 불가 규칙: 점수 보유 종목 ≥ MIN_UNIVERSE 인 리밸런싱이 이보다 적으면 그 시장은 판정하지 않는다


def valid_rebalances(mk: str, cand: str) -> int:
    start, end = _WINDOW["range"]
    P = _PANELS[mk]
    return sum(1 for r in P["reb"] if start <= str(P["close"].index[r + 1].date()) <= end
               and int(P["score"][cand].iloc[r].notna().sum()) >= MIN_UNIVERSE)


def holdout(cand: str, force: bool) -> None:
    if cand != HOLDOUT_CAND:
        raise SystemExit(f"2차 사전 등록은 {HOLDOUT_CAND} 하나만 평가한다 (받은 값: {cand})")
    if HOLDOUT_MARKER.exists() and not force:
        raise SystemExit(f"홀드아웃은 이미 한 번 실행했다 ({HOLDOUT_MARKER}).")
    idx_close = prepare("holdout")
    start, end = _WINDOW["range"]
    jobs = [(mk, cand, seed) for mk in ("KR", "US") for seed in [None, *range(SHUFFLE_RUNS)]]
    print(f"홀드아웃 {start} ~ {end} · 후보 {cand} · 작업 {len(jobs)}개")
    res = run_jobs(jobs)
    ok_all, undecidable = True, []
    for mk in ("KR", "US"):
        j = judge(res, idx_close, mk, cand)
        show(j, mk)
        nv = valid_rebalances(mk, cand)
        if nv < MIN_VALID_REBALANCES:
            undecidable.append(mk)
            print(f"      → 판정 불가 (유효 리밸런싱 {nv}회 < {MIN_VALID_REBALANCES})")
        ok_all = ok_all and j["ok"]
    ok_all = ok_all and not undecidable
    label = "합격" if ok_all else ("미합격(검증 불충분: " + ",".join(undecidable) + ")" if undecidable else "미합격")
    HOLDOUT_MARKER.write_text(f"{datetime.now().isoformat()} {cand} → {label}\n")
    print(f"\n홀드아웃 최종 판정: {label}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["dev", "holdout-check", "holdout"])
    ap.add_argument("--cand")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.command == "dev":
        return dev()
    if a.command == "holdout-check":
        return holdout_check()
    if not a.cand:
        raise SystemExit("--cand 가 필요하다")
    return holdout(a.cand, a.force)


if __name__ == "__main__":
    main()
