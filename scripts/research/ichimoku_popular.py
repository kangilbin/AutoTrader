"""대중적 일목 규칙(상태 기반) + 워크포워드 검증 (연구용 — 실전 코드와 무관)

사전 등록: scripts/research/PREREG_ichimoku_popular.md (결과를 보고 바꾸지 않는다)

진입 A0/A1 구름 돌파(±양운) · B0/B1 상태 성립(±양운) · C 기준선 눌림 재진입
청산 X1 종가 < 구름 상단 · X2 종가 < 구름 하단 · X3 음운 전환 또는 구름 하단 이탈
후보 15개를 훈련 구간에서 평가해 3개를 동결 → 검증 구간 단 1회 → 통과 하나를 홀드아웃 단 1회.

실행 (프로젝트 루트에서, 순서대로 한 번씩):
  PYTHONPATH=. uv run python scripts/research/ichimoku_popular.py train
  PYTHONPATH=. uv run python scripts/research/ichimoku_popular.py validate
  PYTHONPATH=. uv run python scripts/research/ichimoku_popular.py holdout-check
  PYTHONPATH=. uv run python scripts/research/ichimoku_popular.py holdout --cand A1-X2
"""
import argparse
import asyncio
import json
import multiprocessing as mp
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import portfolio_sim as ps  # noqa: E402
from regime_exposure import BASE_KW, SIGNAL_INDEX, index_deposit_cagr_at_mdd, yearly  # noqa: E402
from app.domain.swing.backtest.strategies.base_strategy import fill_buy, fill_sell, sell_tick  # noqa: E402

HERE = Path(__file__).resolve().parent
SEL_JSON = HERE / "ichimoku_popular_train.json"
VALID_JSON = HERE / "ichimoku_popular_valid.json"
VALID_MARKER = HERE / ".valid_ichimoku_popular_done"
HOLDOUT_MARKER = HERE / ".holdout_ichimoku_popular_done"

TENKAN, KIJUN, SENKOU_B, SHIFT = 9, 26, 52, 26     # 표준값 — 튜닝하지 않는다
ENTRIES, EXITS = ("A0", "A1", "B0", "B1", "C"), ("X1", "X2", "X3")
CANDS = [f"{e}-{x}" for e in ENTRIES for x in EXITS]
WINDOWS = {"train": ("2020-05-01", "2023-06-30"), "valid": ("2023-07-01", "2026-10-23"), "holdout": ps.PERIODS["holdout"]}
C2_RUNS = 30
N_SELECT = 3
MIN_CYC = {"train": 40, "valid": 30, "holdout": 60}
TOP_EXCLUDE = {"valid": 5, "holdout": 10}
RECENT_DAYS = 5            # B: 최근 이 기간 안에 성립했고 지금도 유지 중
KIJUN_RISE_BARS = 5        # C: 기준선이 이 봉 전보다 높아야 '상승 중'
STABILITY = 0.5            # 검증 MAR >= 훈련 MAR × 이 값

_DATA: Dict[str, Dict[tuple, Dict[str, np.ndarray]]] = {}
_CLOSES: Dict[str, Dict[tuple, pd.Series]] = {}


# ==================== 지표 / 신호 ====================

def build_arrays(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    """종목 일봉 → 지표·진입/청산 조건 배열 (모든 값은 그 봉의 종가 시점까지의 데이터만 쓴다)"""
    d = df.sort_values("STCK_BSOP_DATE").reset_index(drop=True)
    o, h, lo, c = (d[k].astype(float) for k in ("STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR"))

    tenkan = (h.rolling(TENKAN).max() + lo.rolling(TENKAN).min()) / 2
    kijun = (h.rolling(KIJUN).max() + lo.rolling(KIJUN).min()) / 2
    sa = ((tenkan + kijun) / 2).shift(SHIFT)
    sb = ((h.rolling(SENKOU_B).max() + lo.rolling(SENKOU_B).min()) / 2).shift(SHIFT)
    top, bot = np.maximum(sa, sb), np.minimum(sa, sb)
    c26 = c.shift(26)

    bull = sa > sb
    above = c > top
    s_off = above & (tenkan > kijun)                 # 구름 위 + 전환선 > 기준선
    s_on = s_off & bull

    def recent(s: pd.Series) -> pd.Series:           # 최근 RECENT_DAYS 일이 모두 참이던 상태는 제외 (= 막 성립한 상태)
        prev_all = s.astype(float).shift(1).rolling(RECENT_DAYS).sum() == RECENT_DAYS
        return s & ~prev_all

    a0 = above & (c.shift(1) <= top.shift(1)) & (c > c26)
    cc = bull & above & (kijun > kijun.shift(KIJUN_RISE_BARS)) & (lo <= kijun) & (c > kijun) & (c > o)

    def arr(s: pd.Series) -> np.ndarray:
        return s.to_numpy(dtype=bool)

    return {
        "o": o.to_numpy(), "c": c.to_numpy(),
        "dates": pd.to_datetime(d["STCK_BSOP_DATE"].astype(str)).to_numpy(),
        "adv": ps.avg_daily_amount_before(d).to_numpy(),
        "top": top.to_numpy(), "bot": bot.to_numpy(),
        "A0": arr(a0), "A1": arr(a0 & bull), "B0": arr(recent(s_off)), "B1": arr(recent(s_on)), "C": arr(cc),
        "X1": arr(c < top), "X2": arr(c < bot), "X3": arr((sa < sb) | (c < bot)),
    }


def window_range(arr: Dict[str, np.ndarray], start: str, end: str) -> Optional[Tuple[int, int, int]]:
    """(결정 가능 첫 봉 lo, 마지막 결정 봉 hi, 구간 마지막 봉) — 체결이 구간 안(t+1)에 있도록 hi = 마지막 봉 − 1"""
    dates = arr["dates"]
    s_idx = int(np.searchsorted(dates, np.datetime64(start), side="left"))
    e_idx = int(np.searchsorted(dates, np.datetime64(end), side="right"))
    lo, hi = max(s_idx - 1, 0), e_idx - 2
    return (lo, hi, e_idx - 1) if hi >= lo else None


def extract_trades(arr: Dict[str, np.ndarray], mrkt_code: str, code: str, cand: str, start: str, end: str,
                   roll_seed: Optional[int] = None) -> List[ps.UnitTrade]:
    """구간 안의 단위 거래 기록 — 신호는 t 종가, 체결은 t+1 시가. roll_seed 가 있으면 구간 안에서 진입 신호 열을 원형 이동"""
    rng = window_range(arr, start, end)
    if rng is None:
        return []
    lo, hi, last = rng
    entry, exit_ = cand.split("-")
    sig = arr[entry][lo:hi + 1]
    if roll_seed is not None and len(sig) > 1:
        sig = np.roll(sig, random.Random(f"{roll_seed}:{code}").randrange(1, len(sig)))
    x = arr[exit_]
    line = arr["top"] if exit_ == "X1" else arr["bot"]      # 사이징용 초기 손절선
    o, c, dates, adv = arr["o"], arr["c"], arr["dates"], arr["adv"]

    trades: List[ps.UnitTrade] = []
    cur: Optional[ps.UnitTrade] = None
    for t in range(lo, hi + 1):
        if cur is None:
            if not sig[t - lo] or np.isnan(line[t]):
                continue
            entry_px = fill_buy(float(o[t + 1]), mrkt_code)
            stop = sell_tick(float(line[t]), mrkt_code)
            if not (0 < stop < entry_px):
                continue
            cur = ps.UnitTrade(
                market=ps.market_of(mrkt_code), code=code, mrkt_code=mrkt_code,
                entry_date=pd.Timestamp(dates[t + 1]), entry_price=entry_px, initial_stop=stop, conviction=1.0,
                adv=float(adv[t + 1]) if not np.isnan(adv[t + 1]) else float("nan"))
            continue
        if t > 0 and not np.isnan(line[t - 1]):
            cur.exit_lines[pd.Timestamp(dates[t])] = float(line[t - 1])
        if x[t]:
            cur.events.append((pd.Timestamp(dates[t + 1]), ps.EVENT_EXIT, fill_sell(float(o[t + 1]), mrkt_code), 1.0))
            trades.append(cur)
            cur = None
    if cur is not None:     # 구간 마지막 날 종가로 최종 평가
        cur.events.append((pd.Timestamp(dates[last]), ps.EVENT_FINAL, fill_sell(float(c[last]), mrkt_code), 1.0))
        trades.append(cur)
    return trades


# ==================== 실행 틀 ====================

def prepare(period: str) -> Dict[str, pd.Series]:
    """데이터 적재 + 종목별 배열. period 는 PERIODS 키(dev | holdout) — 적격 종목과 데이터 끝 날짜를 정한다"""
    universe = asyncio.run(ps.load_universe())
    data = ps.tradable(universe)
    end = ps.PERIODS[period][1]
    closes = ps.closes_by_stock(data)
    for mk in ("KR", "US"):
        _DATA[mk], _CLOSES[mk] = {}, {}
        for (m, code), df in data.items():
            if ps.market_of(m) != mk or not ps.is_eligible(df, period):
                continue
            _DATA[mk][(m, code)] = build_arrays(df[df["STCK_BSOP_DATE"] <= end.replace("-", "")])
            _CLOSES[mk][(m, code)] = closes[(m, code)]
    return {mk: ps.closes_by_stock({k: universe[k]})[k] for mk, k in SIGNAL_INDEX.items()}


def run_one(mk: str, cand: str, seed: Optional[int], win: str) -> Optional[Dict]:
    start, end = WINDOWS[win]
    ts: List[ps.UnitTrade] = []
    for (m, code), arr in _DATA[mk].items():
        ts.extend(extract_trades(arr, m, code, cand, start, end, seed))
    if not ts:
        return None
    p = ps.SimParams(equity0=ps.EQUITY0[mk], **{**BASE_KW, "park_apy": ps.PARK_APY[mk]})

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
        n_ex = TOP_EXCLUDE.get(win)
        if n_ex:   # P3: 수익 상위 사이클을 빼고 다시 재생
            drop = {(x["code"], x["entry_date"]) for x in sorted(res.cycles, key=lambda x: -x["pnl"])[:n_ex]}
            rest = [t for t in ts if (t.code, t.entry_date) not in drop]
            m["mar_ex_top"] = ps.sim_metrics(play(rest)).get("mar") if rest else None
    return m


def _job(args):
    mk, cand, seed, win = args
    return mk, cand, seed, win, run_one(mk, cand, seed, win)


def run_jobs(jobs: List[tuple]) -> Dict[tuple, Optional[Dict]]:
    with mp.get_context("fork").Pool() as pool:
        return {(mk, cand, seed, win): m for mk, cand, seed, win, m in pool.map(_job, jobs, chunksize=2)}


def fmt(m: Optional[Dict]) -> str:
    if m is None:
        return "거래 없음"
    return (f"CAGR {m['cagr']:+.1%} · MDD {m['mdd']:.1%} · MAR {m['mar']} · 노출 {m['exposure']:.0%}"
            f" · 사이클 {m['trades']} · 승률 {m['win_rate']} · 평균R {m['mean_r_net']}")


def judge(res: Dict[tuple, Optional[Dict]], idx_close: Dict[str, pd.Series], mk: str, cand: str, win: str,
          train_mar: Optional[float] = None) -> Dict:
    """안정성(훈련 대비)·P1·P2·P3·표본 — 검증과 홀드아웃이 같은 함수"""
    m = res[(mk, cand, None, win)]
    if m is None:
        return {"m": None, "ok": False}
    start, end = WINDOWS[win]
    ref = index_deposit_cagr_at_mdd(idx_close[mk], ps.PARK_APY[mk], start, end, m["mdd"])
    c2 = [(res[(mk, cand, s, win)] or {}).get("mar") for s in range(C2_RUNS)]
    thr = ps.control_threshold(c2)
    out = {"m": m, "ref": ref, "thr": thr,
           "p1": ref is not None and m["cagr"] >= ref,
           "p2": thr is not None and (m["mar"] or 0) >= thr,
           "p3": m.get("mar_ex_top") is not None and m["mar_ex_top"] > 0,
           "sample": m["cycles"] >= MIN_CYC[win],
           "stab": True if train_mar is None else (m["mar"] or 0) > 0 and (m["mar"] or 0) >= STABILITY * train_mar}
    out["ok"] = all(out[k] for k in ("p1", "p2", "p3", "sample", "stab"))
    return out


def show(j: Dict, mk: str, tag: str) -> None:
    if j["m"] is None:
        print(f"[{mk}] 거래 없음 → 불합격")
        return
    print(f"[{mk}] {fmt(j['m'])}\n      연도별 {j['m']['yearly']}"
          f"\n      같은 MDD 지수+예금 CAGR {j['ref']:+.1%} · 대조군 90분위 MAR {None if j['thr'] is None else round(j['thr'], 2)}"
          f" · 상위 제외 MAR {j['m'].get('mar_ex_top')}"
          f"\n      안정성 {'O' if j['stab'] else 'X'}  P1 {'O' if j['p1'] else 'X'}  P2 {'O' if j['p2'] else 'X'}"
          f"  P3 {'O' if j['p3'] else 'X'}  표본 {'O' if j['sample'] else 'X'}")


# ==================== train / validate / holdout ====================

def train(force: bool) -> None:
    if SEL_JSON.exists() and not force:
        raise SystemExit(f"훈련 선택이 이미 동결됐다 ({SEL_JSON}). 결과를 보고 다시 고르지 않는다.")
    prepare("dev")
    start, end = WINDOWS["train"]
    res = run_jobs([(mk, cand, None, "train") for mk in ("KR", "US") for cand in CANDS])
    print(f"훈련 구간 {start} ~ {end}")
    rows = []
    for cand in CANDS:
        kr, us = res[("KR", cand, None, "train")], res[("US", cand, None, "train")]
        if kr is None or us is None:
            continue
        ok = kr["trades"] >= MIN_CYC["train"] and us["trades"] >= MIN_CYC["train"]
        score = min(kr["mar"] or -9, us["mar"] or -9)
        rows.append((cand, score, ok, kr, us))
    rows.sort(key=lambda r: -r[1])
    for cand, score, ok, kr, us in rows:
        print(f"\n{cand}  min-MAR {score:.2f}  {'(표본 부족)' if not ok else ''}\n  [KR] {fmt(kr)}\n  [US] {fmt(us)}")
    chosen = [r for r in rows if r[2]][:N_SELECT]
    SEL_JSON.write_text(json.dumps({
        "at": datetime.now().isoformat(timespec="seconds"),
        "selected": [{"cand": c, "train_mar": {"KR": kr["mar"], "US": us["mar"]}, "score": s} for c, s, _, kr, us in chosen],
        "all": {c: {"KR": kr["mar"], "US": us["mar"]} for c, _, _, kr, us in rows}}, ensure_ascii=False, indent=1))
    print(f"\n동결된 선택(상위 {N_SELECT}): {[c for c, *_ in chosen]} → {SEL_JSON}")


def validate(force: bool) -> None:
    if not SEL_JSON.exists():
        raise SystemExit("먼저 train 을 실행해 선택을 동결해야 한다")
    if VALID_MARKER.exists() and not force:
        raise SystemExit(f"검증 구간은 이미 한 번 열었다 ({VALID_MARKER}). 결과를 보고 후보를 바꾸지 않는다.")
    sel = json.loads(SEL_JSON.read_text())["selected"]
    idx_close = prepare("dev")
    start, end = WINDOWS["valid"]
    jobs = [(mk, s["cand"], seed, "valid") for s in sel for mk in ("KR", "US") for seed in [None, *range(C2_RUNS)]]
    print(f"검증 구간 {start} ~ {end} · 동결 후보 {[s['cand'] for s in sel]} · 작업 {len(jobs)}개")
    res = run_jobs(jobs)
    passed = []
    for s in sel:
        cand = s["cand"]
        print(f"\n=== {cand} (훈련 MAR KR {s['train_mar']['KR']} · US {s['train_mar']['US']}) ===")
        ok_all = True
        for mk in ("KR", "US"):
            j = judge(res, idx_close, mk, cand, "valid", s["train_mar"][mk])
            show(j, mk, cand)
            ok_all = ok_all and j["ok"]
        if ok_all:
            passed.append(cand)
    best = max(passed, key=lambda c: min(res[(mk, c, None, "valid")]["mar"] or -9 for mk in ("KR", "US"))) if passed else None
    VALID_MARKER.write_text(f"{datetime.now().isoformat()} passed={passed} best={best}\n")
    VALID_JSON.write_text(json.dumps({"passed": passed, "best": best}, ensure_ascii=False))
    print(f"\n검증 통과: {passed if passed else '없음'}" + (f" · 홀드아웃 후보 {best}" if best else ""))


def holdout_check() -> None:
    """홀드아웃 신호 수만 점검 — 성과 계산 없음"""
    best = json.loads(VALID_JSON.read_text())["best"] if VALID_JSON.exists() else None
    if not best:
        raise SystemExit("검증 통과 후보가 없어 홀드아웃을 진행하지 않는다")
    prepare("holdout")
    start, end = WINDOWS["holdout"]
    print(f"홀드아웃 {start} ~ {end} 점검 (성과 계산 없음) · 후보 {best}")
    for mk in ("KR", "US"):
        n = sum(len(extract_trades(a, m, c, best, start, end)) for (m, c), a in _DATA[mk].items())
        print(f"[{mk}] 풀 {len(_DATA[mk])}종목 · 신호 기반 거래 기록 {n}건")


def holdout(cand: str, force: bool) -> None:
    best = json.loads(VALID_JSON.read_text())["best"] if VALID_JSON.exists() else None
    if cand != best:
        raise SystemExit(f"홀드아웃은 검증을 통과한 후보({best}) 하나만 평가한다 (받은 값: {cand})")
    if HOLDOUT_MARKER.exists() and not force:
        raise SystemExit(f"홀드아웃은 이미 한 번 실행했다 ({HOLDOUT_MARKER}).")
    idx_close = prepare("holdout")
    jobs = [(mk, cand, seed, "holdout") for mk in ("KR", "US") for seed in [None, *range(C2_RUNS)]]
    start, end = WINDOWS["holdout"]
    print(f"홀드아웃 {start} ~ {end} · 후보 {cand} · 작업 {len(jobs)}개")
    res = run_jobs(jobs)
    ok_all = True
    for mk in ("KR", "US"):
        j = judge(res, idx_close, mk, cand, "holdout")
        show(j, mk, cand)
        ok_all = ok_all and j["ok"]
    label = "합격" if ok_all else "미합격"
    HOLDOUT_MARKER.write_text(f"{datetime.now().isoformat()} {cand} → {label}\n")
    print(f"\n홀드아웃 최종 판정: {label}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["train", "validate", "holdout-check", "holdout"])
    ap.add_argument("--cand")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.command == "train":
        return train(a.force)
    if a.command == "validate":
        return validate(a.force)
    if a.command == "holdout-check":
        return holdout_check()
    if not a.cand:
        raise SystemExit("--cand 가 필요하다")
    return holdout(a.cand, a.force)


if __name__ == "__main__":
    main()
