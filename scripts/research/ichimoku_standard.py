"""표준 일목균형표(삼역호전/역전) 전략 검증 (연구용 — 실전 코드와 무관)

사전 등록: scripts/research/PREREG_ichimoku_standard.md (결과를 보고 바꾸지 않는다)

신호는 t 종가에 판정하고 t+1 시가에 체결한다. 표준 기간 9/26/52 고정.
  진입  E1 삼역호전(세 조건이 오늘 처음 모두 충족) | E2 TK 상향교차 + 종가 > 구름 상단
  청산  X1 종가 < 구름 하단 | X2 종가 < 기준선 | X3 삼역역전
후보 6개(E×X)를 풀 구조 재생 엔진으로 평가하고, "같은 MDD 의 지수 + 예금" 선·무작위 시점 대조군과 비교한다.

실행 (프로젝트 루트에서):
  PYTHONPATH=. uv run python scripts/research/ichimoku_standard.py dev
  PYTHONPATH=. uv run python scripts/research/ichimoku_standard.py holdout-check     # 성과 없이 신호 수만 점검
  PYTHONPATH=. uv run python scripts/research/ichimoku_standard.py holdout --cand E1-X1   # 합격 후보 단 1회
"""
import argparse
import asyncio
import json
import multiprocessing as mp
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import portfolio_sim as ps  # noqa: E402
from regime_exposure import BASE_KW, SIGNAL_INDEX, index_deposit_cagr_at_mdd, yearly  # noqa: E402
from app.domain.swing.backtest.strategies.base_strategy import fill_buy, fill_sell, sell_tick  # noqa: E402

HOLDOUT_MARKER = Path(__file__).resolve().parent / ".holdout_ichimoku_std_done"
DEV_OUT = Path(__file__).resolve().parent / "ichimoku_standard_dev.json"

TENKAN, KIJUN, SENKOU_B, SHIFT = 9, 26, 52, 26     # 표준값 — 튜닝하지 않는다
ENTRIES, EXITS = ("E1", "E2"), ("X1", "X2", "X3")
CANDS = [f"{e}-{x}" for e in ENTRIES for x in EXITS]
C2_RUNS = 30
MIN_CYCLES = 60            # 표본 규칙: 한 시장이라도 체결 사이클이 이보다 적으면 불합격
TOP_EXCLUDE = 10           # P3: 수익 상위 사이클을 이만큼 빼고 다시 재생

_DATA: Dict[str, Dict[tuple, Dict[str, np.ndarray]]] = {}     # 시장 → 종목 → 지표·신호 배열 (fork 로 워커에 공유)
_CLOSES: Dict[str, Dict[tuple, pd.Series]] = {}


# ==================== 지표 / 신호 ====================

def build_arrays(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    """종목 일봉 → 지표·진입/청산 조건 배열. 모든 값은 그 봉의 종가 시점까지의 데이터만 쓴다 (선행스팬은 26봉 전 계산값)"""
    d = df.sort_values("STCK_BSOP_DATE").reset_index(drop=True)
    o = d["STCK_OPRC"].astype(float)
    h = d["STCK_HGPR"].astype(float)
    lo = d["STCK_LWPR"].astype(float)
    c = d["STCK_CLPR"].astype(float)

    tenkan = (h.rolling(TENKAN).max() + lo.rolling(TENKAN).min()) / 2
    kijun = (h.rolling(KIJUN).max() + lo.rolling(KIJUN).min()) / 2
    sa = ((tenkan + kijun) / 2).shift(SHIFT)
    sb = ((h.rolling(SENKOU_B).max() + lo.rolling(SENKOU_B).min()) / 2).shift(SHIFT)
    top, bot = np.maximum(sa, sb), np.minimum(sa, sb)
    c26 = c.shift(26)

    bull = (tenkan > kijun) & (c > top) & (c > c26)                      # 삼역호전 상태
    e1 = bull & ~bull.shift(1, fill_value=False)                          # 오늘 처음 충족
    e2 = (tenkan > kijun) & (tenkan.shift(1) <= kijun.shift(1)) & (c > top)
    x1 = c < bot
    x2 = c < kijun
    x3 = (tenkan < kijun) & (c < bot) & (c < c26)                         # 삼역역전

    return {
        "o": o.to_numpy(), "c": c.to_numpy(),
        "dates": pd.to_datetime(d["STCK_BSOP_DATE"].astype(str)).to_numpy(),
        "adv": ps.avg_daily_amount_before(d).to_numpy(),
        "kijun": kijun.to_numpy(), "bot": bot.to_numpy(),
        "E1": e1.to_numpy(dtype=bool), "E2": e2.to_numpy(dtype=bool),
        "X1": x1.to_numpy(dtype=bool), "X2": x2.to_numpy(dtype=bool), "X3": x3.to_numpy(dtype=bool),
    }


def extract_trades(arr: Dict[str, np.ndarray], mrkt_code: str, code: str, cand: str, start: np.datetime64,
                   roll: int = 0) -> List[ps.UnitTrade]:
    """종목 하나의 단위 거래 기록 — 신호는 t 종가, 체결은 t+1 시가. roll != 0 이면 진입 신호 열을 원형 이동(무작위 시점 대조군)"""
    entry, exit_ = cand.split("-")
    e = np.roll(arr[entry], roll) if roll else arr[entry]
    x = arr[exit_]
    line = arr["kijun"] if exit_ == "X2" else arr["bot"]      # 사이징용 초기 손절선 (X1·X3 구름 하단, X2 기준선)
    o, c, dates, adv = arr["o"], arr["c"], arr["dates"], arr["adv"]
    n = len(c)

    trades: List[ps.UnitTrade] = []
    cur: Optional[ps.UnitTrade] = None
    for t in range(n - 1):
        if cur is None:
            if not e[t] or dates[t + 1] < start or np.isnan(line[t]):
                continue
            entry_px = fill_buy(float(o[t + 1]), mrkt_code)
            stop = sell_tick(float(line[t]), mrkt_code)
            if not (0 < stop < entry_px):
                continue
            cur = ps.UnitTrade(
                market=ps.market_of(mrkt_code), code=code, mrkt_code=mrkt_code,
                entry_date=pd.Timestamp(dates[t + 1]), entry_price=entry_px, initial_stop=stop,
                conviction=1.0, adv=float(adv[t + 1]) if not np.isnan(adv[t + 1]) else float("nan"))
            continue
        if not np.isnan(line[t - 1]):
            cur.exit_lines[pd.Timestamp(dates[t])] = float(line[t - 1])
        if x[t]:
            cur.events.append((pd.Timestamp(dates[t + 1]), ps.EVENT_EXIT, fill_sell(float(o[t + 1]), mrkt_code), 1.0))
            trades.append(cur)
            cur = None
    if cur is not None:     # 구간 마지막 날 종가로 최종 평가
        cur.events.append((pd.Timestamp(dates[n - 1]), ps.EVENT_FINAL, fill_sell(float(c[n - 1]), mrkt_code), 1.0))
        trades.append(cur)
    return trades


# ==================== 실행 틀 ====================

def prepare(period: str) -> Dict[str, pd.Series]:
    """데이터 적재 + 종목별 배열 계산. 반환: 시장 → 신호 지수 종가 (같은 MDD 선 계산용)"""
    universe = asyncio.run(ps.load_universe())
    data = ps.tradable(universe)
    start, end = ps.PERIODS[period]
    ps._SHARED.update(range=(start, end), data=data, period=period)
    closes = ps.closes_by_stock(data)
    for mk in ("KR", "US"):
        _DATA[mk], _CLOSES[mk] = {}, {}
        for (m, code), df in data.items():
            if ps.market_of(m) != mk or not ps.is_eligible(df, period):
                continue
            sub = df[df["STCK_BSOP_DATE"] <= end.replace("-", "")]
            _DATA[mk][(m, code)] = build_arrays(sub)
            _CLOSES[mk][(m, code)] = closes[(m, code)]
    return {mk: ps.closes_by_stock({k: universe[k]})[k] for mk, k in SIGNAL_INDEX.items()}


def sim_kw(mk: str) -> Dict:
    return {**BASE_KW, "park_apy": ps.PARK_APY[mk]}


def run_one(mk: str, cand: str, seed: Optional[int]) -> Optional[Dict]:
    """후보 하나(seed=None 이면 실제 신호, 아니면 무작위 시점 대조군)를 시장 하나에서 재생"""
    start = np.datetime64(ps._SHARED["range"][0])
    ts: List[ps.UnitTrade] = []
    for (m, code), arr in _DATA[mk].items():
        roll = 0 if seed is None else random.Random(f"{seed}:{code}").randrange(1, len(arr["c"]))
        ts.extend(extract_trades(arr, m, code, cand, start, roll))
    if not ts:
        return None
    keys = {(t.mrkt_code, t.code) for t in ts}
    p = ps.SimParams(equity0=ps.EQUITY0[mk], **sim_kw(mk))
    res = ps.simulate(ts, {k: v for k, v in _CLOSES[mk].items() if k in keys}, p, *ps._SHARED["range"])
    m = ps.sim_metrics(res)
    if m.get("days") == 0:
        return None
    m["yearly"] = yearly(res.curve["equity"], p.equity0)
    if seed is None:    # P3: 수익 상위 사이클을 빼고 다시 재생
        top = sorted(res.cycles, key=lambda x: -x["pnl"])[:TOP_EXCLUDE]
        drop = {(x["code"], x["entry_date"]) for x in top}
        ts2 = [t for t in ts if (t.code, t.entry_date) not in drop]
        keys2 = {(t.mrkt_code, t.code) for t in ts2}
        res2 = ps.simulate(ts2, {k: v for k, v in _CLOSES[mk].items() if k in keys2}, p, *ps._SHARED["range"]) if ts2 else None
        m["mar_ex_top"] = ps.sim_metrics(res2).get("mar") if res2 is not None else None
        m["cycles"] = len(res.cycles)
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
            f" · 사이클 {m['trades']} · 승률 {m['win_rate']} · 평균R {m['mean_r_net']}")


def judge(res: Dict[tuple, Optional[Dict]], idx_close: Dict[str, pd.Series], mk: str, cand: str) -> Dict:
    """P1~P3 + 표본 규칙 (개발·홀드아웃이 같은 함수)"""
    start, end = ps.PERIODS[_period()]
    m = res[(mk, cand, None)]
    if m is None:
        return {"m": None, "ok": False, "p1": False, "p2": False, "p3": False, "sample": False}
    ref = index_deposit_cagr_at_mdd(idx_close[mk], ps.PARK_APY[mk], start, end, m["mdd"])
    c2 = [(res[(mk, cand, s)] or {}).get("mar") for s in range(C2_RUNS)]
    thr = ps.control_threshold(c2)
    p1 = ref is not None and m["cagr"] >= ref
    p2 = thr is not None and (m["mar"] or 0) >= thr
    p3 = m.get("mar_ex_top") is not None and m["mar_ex_top"] > 0
    sample = m["cycles"] >= MIN_CYCLES
    return {"m": m, "ref": ref, "thr": thr, "p1": p1, "p2": p2, "p3": p3, "sample": sample, "ok": p1 and p2 and p3 and sample}


def _period() -> str:
    return ps._SHARED["period"]


# ==================== dev ====================

def dev() -> None:
    idx_close = prepare("dev")
    start, end = ps.PERIODS["dev"]
    for mk in ("KR", "US"):
        print(f"[{mk}] 풀 {len(_DATA[mk])}종목 · E1 신호 {sum(int(a['E1'].sum()) for a in _DATA[mk].values())}"
              f" · E2 신호 {sum(int(a['E2'].sum()) for a in _DATA[mk].values())} (전체 이력, 구간 외 포함)")
    jobs = [(mk, cand, seed) for mk in ("KR", "US") for cand in CANDS for seed in [None, *range(C2_RUNS)]]
    print(f"개발 구간 {start} ~ {end} · 작업 {len(jobs)}개 실행")
    res = run_jobs(jobs)

    print("\n=== 참고 기준선 B0 (현행 20EMA 풀 + 파킹) ===")
    ema_trades = ps.extract_all(ps._SHARED["data"], "dev")
    for mk in ("KR", "US"):
        ts = [t for t in ema_trades if t.market == mk]
        keys = {(t.mrkt_code, t.code) for t in ts}
        p = ps.SimParams(equity0=ps.EQUITY0[mk], **sim_kw(mk))
        r = ps.simulate(ts, {k: v for k, v in _CLOSES[mk].items() if k in keys}, p, start, end)
        m = ps.sim_metrics(r)
        ref = index_deposit_cagr_at_mdd(idx_close[mk], ps.PARK_APY[mk], start, end, m["mdd"])
        print(f"[{mk}] {fmt(m)}\n      같은 MDD 의 지수+예금 CAGR {ref:+.1%}")

    passed = []
    for cand in CANDS:
        print(f"\n=== 후보 {cand} ===")
        ok_all = True
        for mk in ("KR", "US"):
            j = judge(res, idx_close, mk, cand)
            ok_all = ok_all and j["ok"]
            if j["m"] is None:
                print(f"[{mk}] 거래 없음 → 불합격")
                continue
            print(f"[{mk}] {fmt(j['m'])}\n      연도별 {j['m']['yearly']}"
                  f"\n      같은 MDD 지수+예금 CAGR {j['ref']:+.1%} · C2 90분위 MAR {None if j['thr'] is None else round(j['thr'], 2)}"
                  f" · 상위 {TOP_EXCLUDE}개 제외 MAR {j['m']['mar_ex_top']}"
                  f"\n      P1 {'O' if j['p1'] else 'X'}  P2 {'O' if j['p2'] else 'X'}  P3 {'O' if j['p3'] else 'X'}"
                  f"  표본(≥{MIN_CYCLES}) {'O' if j['sample'] else 'X'}")
        if ok_all:
            passed.append(cand)
    print("\n=== 판정 ===")
    print("합격:", passed if passed else "없음")
    best = None
    if passed:
        best = max(passed, key=lambda c: min(res[(mk, c, None)]["mar"] or -9 for mk in ("KR", "US")))
        print(f"홀드아웃 후보(최고 min-MAR): {best} → holdout --cand {best}")
    DEV_OUT.write_text(json.dumps({
        "at": datetime.now().isoformat(timespec="seconds"), "passed": passed, "best": best,
        "results": {f"{mk}/{cand}": res[(mk, cand, None)] for mk in ("KR", "US") for cand in CANDS},
        "c2_mars": {f"{mk}/{cand}": [(res[(mk, cand, s)] or {}).get("mar") for s in range(C2_RUNS)]
                    for mk in ("KR", "US") for cand in CANDS}}, ensure_ascii=False, indent=1, default=str))
    print(f"저장: {DEV_OUT}")


# ==================== holdout ====================

def holdout_check() -> None:
    """홀드아웃 구간의 종목 수·신호 수만 점검 — 성과는 계산하지 않는다"""
    prepare("holdout")
    start, end = ps.PERIODS["holdout"]
    s64 = np.datetime64(start)
    print(f"홀드아웃 {start} ~ {end} 점검 (성과 계산 없음)")
    for mk in ("KR", "US"):
        e1 = sum(int(((a["dates"] >= s64) & a["E1"]).sum()) for a in _DATA[mk].values())
        e2 = sum(int(((a["dates"] >= s64) & a["E2"]).sum()) for a in _DATA[mk].values())
        print(f"[{mk}] 풀 {len(_DATA[mk])}종목 · 구간 내 E1 신호 {e1} · E2 신호 {e2}")


def holdout(cand: str, force: bool) -> None:
    if cand not in CANDS:
        raise SystemExit(f"알 수 없는 후보: {cand}")
    if HOLDOUT_MARKER.exists() and not force:
        raise SystemExit(f"홀드아웃은 이미 한 번 실행했다 ({HOLDOUT_MARKER}). 결과를 보고 설정을 바꿔 다시 맞추지 않는다.")
    idx_close = prepare("holdout")
    start, end = ps.PERIODS["holdout"]
    jobs = [(mk, cand, seed) for mk in ("KR", "US") for seed in [None, *range(C2_RUNS)]]
    print(f"홀드아웃 {start} ~ {end} · 후보 {cand} · 작업 {len(jobs)}개")
    res = run_jobs(jobs)
    ok_all = True
    for mk in ("KR", "US"):
        j = judge(res, idx_close, mk, cand)
        ok_all = ok_all and j["ok"]
        if j["m"] is None:
            print(f"[{mk}] 거래 없음 → 불합격")
            continue
        print(f"\n[{mk}] {fmt(j['m'])}\n      연도별 {j['m']['yearly']}"
              f"\n      같은 MDD 지수+예금 CAGR {j['ref']:+.1%} · C2 90분위 MAR {None if j['thr'] is None else round(j['thr'], 2)}"
              f" · 상위 {TOP_EXCLUDE}개 제외 MAR {j['m']['mar_ex_top']}"
              f"\n      P1 {'O' if j['p1'] else 'X'}  P2 {'O' if j['p2'] else 'X'}  P3 {'O' if j['p3'] else 'X'}"
              f"  표본(≥{MIN_CYCLES}) {'O' if j['sample'] else 'X'}")
    label = "합격" if ok_all else "미합격"
    HOLDOUT_MARKER.write_text(f"{datetime.now().isoformat()} {cand} → {label}\n")
    print(f"\n홀드아웃 최종 판정: {label}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["dev", "holdout-check", "holdout"])
    ap.add_argument("--cand", help="holdout 전용 — 후보 이름 (예: E1-X1)")
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
