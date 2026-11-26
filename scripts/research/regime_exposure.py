"""시장(지수) 상태 기반 신규 매수 노출 조절 연구 (연구용 — 실전 코드와 무관)

사전 등록: scripts/research/PREREG_regime_exposure.md (결과를 보고 바꾸지 않는다)

지수를 사지 않는다 — 지수 종가는 약세 여부를 읽는 신호로만 쓴다. 약세 상태에서는 신규 매수 크기를 줄이거나(M1)
중단한다(M2). 보유 중인 포지션과 청산은 그대로다. 비는 자금은 파킹 수익(park_apy)을 받는다.

실행 (프로젝트 루트에서):
  PYTHONPATH=. uv run python scripts/research/regime_exposure.py regress        # 엔진 수정 전후 동일성 점검
  PYTHONPATH=. uv run python scripts/research/regime_exposure.py dev            # 후보 6 + 대조군 + 이웃
  PYTHONPATH=. uv run python scripts/research/regime_exposure.py holdout --cand G1-M1   # 합격 후보 단 1회
"""
import argparse
import asyncio
import json
import math
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

HOLDOUT_MARKER = Path(__file__).resolve().parent / ".holdout_regime_done"
DEV_OUT = Path(__file__).resolve().parent / "regime_exposure_dev.json"

SIGNAL_INDEX = {"KR": ("J", "069500"), "US": ("AMS", "SPY")}
BASE_KW = {"mode": "pool", "risk_pct": 0.007, "max_position_pct": 0.095, "max_positions": 10, "priority": "narrow_stop"}

SIGNALS = {"G1": 200, "G2": 0.10, "G3": 1.5}                  # 신호 → 기본 파라미터
NEIGHBORS = {"G1": [150, 250], "G2": [0.075, 0.125], "G3": [1.25, 1.75]}
MAPS = {"M1": 0.5, "M2": 0.0}                                  # 약세 시 신규 매수 배율
C2_RUNS = 30

REGRESSION_ANCHOR = {   # park_apy=0, 배율 없음 — 엔진 수정 전 현행 EMA 풀 결과 (총수익 · MDD · 거래 수)
    "KR": (0.872, 0.401, 396),
    "US": (0.782, 0.258, 239),
}


# ==================== 신호 ====================

def weak_series(close: pd.Series, sig: str, param: float) -> pd.Series:
    """지수 종가 → 날짜별 '그 날 종가 기준 약세' (미래 봉을 쓰지 않는다). 계산 불가 구간은 약세 아님"""
    if sig == "G1":
        weak = close < close.rolling(int(param), min_periods=int(param)).mean()
    elif sig == "G2":
        weak = close <= close.rolling(252, min_periods=252).max() * (1 - param)
    elif sig == "G3":
        vol = close.pct_change().rolling(20, min_periods=20).std() * math.sqrt(252)
        weak = vol > param * vol.rolling(252, min_periods=252).median()
    else:
        raise ValueError(f"알 수 없는 신호: {sig}")
    return weak.fillna(False).astype(bool)


def lagged_state(weak: pd.Series, dates: List[pd.Timestamp]) -> np.ndarray:
    """날짜 d 의 약세 여부 = d **이전** 마지막 지수 종가 기준 (1거래일 지연 — 당일 종가로 당일 매수를 정하는 선견 금지)"""
    idx = weak.index.values
    vals = weak.to_numpy(dtype=bool)
    pos = np.searchsorted(idx, np.array(dates, dtype="datetime64[ns]"), side="left") - 1
    return np.where(pos >= 0, vals[np.clip(pos, 0, None)], False)


def scale_map(dates: List[pd.Timestamp], state: np.ndarray, s_weak: float) -> Dict[pd.Timestamp, float]:
    return {d: (s_weak if st else 1.0) for d, st in zip(dates, state)}


# ==================== 지표 보조 ====================

def yearly(equity: pd.Series, e0: float) -> Dict[int, float]:
    ye = equity.resample("YE").last()
    r = pd.concat([pd.Series([e0]), ye.reset_index(drop=True)]).pct_change().dropna()
    return {int(y): round(float(v), 3) for y, v in zip(ye.index.year, r)}


def index_deposit_cagr_at_mdd(close: pd.Series, dep_apy: float, start: str, end: str, target_mdd: float) -> Optional[float]:
    """같은 MDD 가 되는 '지수 w + 예금' 단순 비중 조절의 CAGR (매일 리밸런싱·비용 0 가정) — 보고용 참고선"""
    s = close[(close.index >= pd.Timestamp(start)) & (close.index <= pd.Timestamp(end))]
    r = s.pct_change().fillna(0.0).to_numpy()
    dep = (1 + dep_apy) ** (1 / 252) - 1
    years = max((s.index[-1] - s.index[0]).days, 1) / 365.25

    def stats(w):
        eq = np.cumprod(1 + w * r + (1 - w) * dep)
        peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
        return eq[-1] ** (1 / years) - 1, float(((peak - eq) / peak).max())

    if stats(1.0)[1] <= target_mdd:
        return stats(1.0)[0]
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if stats(mid)[1] > target_mdd:
            hi = mid
        else:
            lo = mid
    return stats(lo)[0]


# ==================== 실행 틀 ====================

def prepare(period: str) -> Dict[str, pd.Series]:
    """거래 기록 추출 + 공유 상태 채우기 (portfolio_sim._SHARED 와 같은 형식). 반환: 시장 → 신호 지수 종가"""
    universe = asyncio.run(ps.load_universe())
    data = ps.tradable(universe)
    trades = ps.extract_all(data, period)
    closes = ps.closes_by_stock(data)
    by_market = {}
    for mk in ("KR", "US"):
        ts = [t for t in trades if t.market == mk]
        keys = {(t.mrkt_code, t.code) for t in ts}
        eligible = sum(1 for (m, _), df in data.items() if ps.market_of(m) == mk and ps.is_eligible(df, period))
        by_market[mk] = {"trades": ts, "closes": {k: v for k, v in closes.items() if k in keys}, "n_stocks": eligible}
    ps._SHARED.update(markets=by_market, range=ps.PERIODS[period])
    return {mk: ps.closes_by_stock({k: universe[k]})[k] for mk, k in SIGNAL_INDEX.items()}


def sim_dates(mk: str) -> List[pd.Timestamp]:
    start, end = ps._SHARED["range"]
    closes = ps._SHARED["markets"][mk]["closes"]
    return sorted({d for s in closes.values() for d in s.index if pd.Timestamp(start) <= d <= pd.Timestamp(end)})


def _job(args):
    mk, name, kw = args
    sh = ps._SHARED["markets"][mk]
    p = ps.ps_params(mk, sh, kw)
    res = ps.simulate(sh["trades"], sh["closes"], p, *ps._SHARED["range"])
    m = ps.sim_metrics(res)
    m["yearly"] = yearly(res.curve["equity"], p.equity0)
    return mk, name, m


def run_jobs(jobs: List[tuple]) -> Dict[tuple, Dict]:
    with mp.get_context("fork").Pool() as pool:
        return {(mk, name): m for mk, name, m in pool.map(_job, jobs, chunksize=4)}


def base_kw(mk: str, park: bool = True, scale: Optional[Dict] = None) -> Dict:
    kw = dict(BASE_KW)
    if park:
        kw["park_apy"] = ps.PARK_APY[mk]
    if scale is not None:
        kw["exposure_scale"] = scale
    return kw


def fmt(m: Dict) -> str:
    return (f"CAGR {m['cagr']:+.1%} · MDD {m['mdd']:.1%} · MAR {m['mar']} · 노출 {m['exposure']:.0%}"
            f" · 거래 {m['trades']} · 건너뜀 {m['skipped']}")


# ==================== regress ====================

def regress() -> None:
    prepare("dev")
    res = run_jobs([(mk, "anchor", base_kw(mk, park=False)) for mk in ("KR", "US")])
    ok = True
    for mk in ("KR", "US"):
        m = res[(mk, "anchor")]
        total, mdd, n = REGRESSION_ANCHOR[mk]
        good = abs(m["total_return"] - total) < 0.0006 and abs(m["mdd"] - mdd) < 0.0006 and m["trades"] == n
        ok = ok and good
        print(f"[{mk}] 총 {m['total_return']:+.4f} (기준 {total:+.3f}) · MDD {m['mdd']:.4f} (기준 {mdd}) · 거래 {m['trades']} (기준 {n}) → {'일치' if good else '불일치'}")
    raise SystemExit(0 if ok else 1)


# ==================== dev ====================

def build_jobs(idx_close: Dict[str, pd.Series], period: str, only_cand: Optional[str] = None,
               controls: Optional[bool] = None):
    """(작업 목록, 후보별 약세 비율, 시장별 날짜) — 후보·정적 대조군·무작위 대조군·이웃을 모두 만든다

    controls: 대조군·이웃까지 만들지 (기본: 개발 구간만). 홀드아웃 재검증(2차 사전 등록)은 True 로 부른다.
    """
    controls = (period == "dev") if controls is None else controls
    jobs, f_weak, dates_of = [], {}, {}
    for mk in ("KR", "US"):
        dates = sim_dates(mk)
        dates_of[mk] = dates
        jobs.append((mk, "B0", base_kw(mk)))
        for sig, param in SIGNALS.items():
            state = lagged_state(weak_series(idx_close[mk], sig, param), dates)
            f_weak[(mk, sig)] = float(state.mean())
            for mname, s in MAPS.items():
                cand = f"{sig}-{mname}"
                if only_cand and cand != only_cand:
                    continue
                jobs.append((mk, cand, base_kw(mk, scale=scale_map(dates, state, s))))
                if not controls:
                    continue
                const = 1 - f_weak[(mk, sig)] * (1 - s)
                jobs.append((mk, f"C1:{cand}", base_kw(mk, scale={d: const for d in dates})))
                for seed in range(C2_RUNS):
                    off = random.Random(seed).randrange(1, len(state))
                    jobs.append((mk, f"C2:{cand}:{seed}", base_kw(mk, scale=scale_map(dates, np.roll(state, off), s))))
                for nb in NEIGHBORS[sig]:
                    st_nb = lagged_state(weak_series(idx_close[mk], sig, nb), dates)
                    jobs.append((mk, f"N:{cand}:{nb}", base_kw(mk, scale=scale_map(dates, st_nb, s))))
    return jobs, f_weak, dates_of


def judge(res: Dict[tuple, Dict], mk: str, cand: str) -> Dict:
    """K1~K3 판정 — 개발 구간과 홀드아웃 재검증이 같은 함수를 쓴다 (기준을 따로 구현하지 않는다)"""
    sig = cand.split("-")[0]
    b0, m = res[(mk, "B0")], res[(mk, cand)]
    c1 = res[(mk, f"C1:{cand}")]
    c2 = [res[(mk, f"C2:{cand}:{s}")]["mar"] for s in range(C2_RUNS)]
    thr = ps.control_threshold(c2)
    nbs = [res[(mk, f"N:{cand}:{nb}")] for nb in NEIGHBORS[sig]]
    return {"b0": b0, "m": m, "c1": c1, "thr": thr, "nbs": nbs,
            "k1": ps.passes_mar_ratio(m["mar"], b0["mar"]),
            "k2": (m["mar"] or 0) >= (c1["mar"] or 0) and thr is not None and (m["mar"] or 0) >= thr,
            "k3": all(ps.passes_mar_ratio(n["mar"], b0["mar"]) for n in nbs)}


def dev() -> None:
    idx_close = prepare("dev")
    start, end = ps.PERIODS["dev"]
    jobs, f_weak, _ = build_jobs(idx_close, "dev")
    print(f"개발 구간 {start} ~ {end} · 작업 {len(jobs)}개 실행")
    res = run_jobs(jobs)

    print("\n=== 기준선 B0 (풀 + 파킹 수익) ===")
    for mk in ("KR", "US"):
        m = res[(mk, "B0")]
        ref = index_deposit_cagr_at_mdd(idx_close[mk], ps.PARK_APY[mk], start, end, m["mdd"])
        print(f"[{mk}] {fmt(m)}\n      연도별 {m['yearly']}\n      같은 MDD 의 지수+예금 CAGR {ref:+.1%}")

    passed, verdict = [], {}
    for sig in SIGNALS:
        for mname in MAPS:
            cand = f"{sig}-{mname}"
            print(f"\n=== 후보 {cand} (약세 신호 {sig}={SIGNALS[sig]}, 약세 시 신규 매수 ×{MAPS[mname]}) ===")
            ok_all, rows = True, {}
            for mk in ("KR", "US"):
                j = judge(res, mk, cand)
                m, c1, thr, nbs, k1, k2, k3 = j["m"], j["c1"], j["thr"], j["nbs"], j["k1"], j["k2"], j["k3"]
                ok_all = ok_all and k1 and k2 and k3
                rows[mk] = (k1, k2, k3)
                ref = index_deposit_cagr_at_mdd(idx_close[mk], ps.PARK_APY[mk], start, end, m["mdd"])
                print(f"[{mk}] 약세 비율 {f_weak[(mk, sig)]:.0%} · {fmt(m)}\n      연도별 {m['yearly']}"
                      f"\n      C1(정적) MAR {c1['mar']} · C2 90분위 MAR {None if thr is None else round(thr, 2)}"
                      f" · 이웃 MAR {[n['mar'] for n in nbs]} · 같은 MDD 지수+예금 CAGR {ref:+.1%}"
                      f"\n      K1 {'O' if k1 else 'X'}  K2 {'O' if k2 else 'X'}  K3 {'O' if k3 else 'X'}")
            verdict[cand] = ok_all
            if ok_all:
                passed.append(cand)
    print("\n=== 판정 ===")
    print("합격:", passed if passed else "없음")
    best = None
    if passed:
        best = max(passed, key=lambda c: min(res[(mk, c)]["mar"] or -9 for mk in ("KR", "US")))
        print(f"홀드아웃 후보(최고 min-MAR): {best} → holdout --cand {best}")
    DEV_OUT.write_text(json.dumps({
        "at": datetime.now().isoformat(timespec="seconds"), "passed": passed, "best": best,
        "results": {f"{mk}/{name}": m for (mk, name), m in res.items() if not name.startswith("C2:")},
        "c2_mars": {f"{mk}/{cand}": [res[(mk, f'C2:{cand}:{s}')]["mar"] for s in range(C2_RUNS)]
                    for mk in ("KR", "US") for cand in verdict}}, ensure_ascii=False, indent=1, default=str))
    print(f"저장: {DEV_OUT}")


# ==================== holdout ====================

HOLDOUT_CAND = "G1-M2"      # 2차 사전 등록(PREREG_regime_exposure_2.md) — 이 후보 하나만 검증한다
MIN_B0_TRADES = 100          # 판정 불가 규칙: B0 거래 수가 이보다 적으면 그 시장은 판정하지 않는다
MIN_WEAK_RATIO = 0.05        # 판정 불가 규칙: 약세로 판정된 거래일 비율이 이보다 낮으면 판정하지 않는다


def holdout_check() -> None:
    """홀드아웃 데이터 충분성만 점검 — 종목 수·거래 수·약세 비율. 성과(MAR·수익)는 계산하지 않는다"""
    idx_close = prepare("holdout")
    start, end = ps.PERIODS["holdout"]
    print(f"홀드아웃 {start} ~ {end} 데이터 점검 (성과 계산 없음)")
    for mk in ("KR", "US"):
        sh = ps._SHARED["markets"][mk]
        dates = sim_dates(mk)
        state = lagged_state(weak_series(idx_close[mk], "G1", SIGNALS["G1"]), dates)
        print(f"[{mk}] 풀 {sh['n_stocks']}종목 · 거래 기록 {len(sh['trades'])}건 · 거래일 {len(dates)}일"
              f" · G1 약세 비율 {state.mean():.1%} · 신호 지수 {idx_close[mk].index[0].date()} ~ {idx_close[mk].index[-1].date()}")
        print(f"      판정 가능: 거래 기록 ≥ {MIN_B0_TRADES} {'O' if len(sh['trades']) >= MIN_B0_TRADES else 'X'}"
              f" · 약세 비율 ≥ {MIN_WEAK_RATIO:.0%} {'O' if state.mean() >= MIN_WEAK_RATIO else 'X'}"
              " (거래 기록 수는 풀 한도 적용 전 후보 수라 B0 체결 수와 다를 수 있다)")


def holdout(cand: str, force: bool) -> None:
    if cand != HOLDOUT_CAND:
        raise SystemExit(f"2차 사전 등록은 {HOLDOUT_CAND} 하나만 검증한다 (받은 값: {cand})")
    if HOLDOUT_MARKER.exists() and not force:
        raise SystemExit(f"홀드아웃은 이미 한 번 실행했다 ({HOLDOUT_MARKER}). 결과를 보고 설정을 바꿔 다시 맞추지 않는다.")
    idx_close = prepare("holdout")
    start, end = ps.PERIODS["holdout"]
    jobs, f_weak, _ = build_jobs(idx_close, "holdout", only_cand=cand, controls=True)
    print(f"홀드아웃 {start} ~ {end} · 후보 {cand} · 작업 {len(jobs)}개")
    res = run_jobs(jobs)
    sig = cand.split("-")[0]
    verdicts, undecidable = {}, []
    for mk in ("KR", "US"):
        j = judge(res, mk, cand)
        b0, m = j["b0"], j["m"]
        weak = f_weak[(mk, sig)]
        ref = index_deposit_cagr_at_mdd(idx_close[mk], ps.PARK_APY[mk], start, end, m["mdd"])
        print(f"\n[{mk}] B0   {fmt(b0)}\n      연도별 {b0['yearly']}"
              f"\n[{mk}] 후보 {fmt(m)} · 약세 비율 {weak:.0%}\n      연도별 {m['yearly']}"
              f"\n      C1(정적) MAR {j['c1']['mar']} · C2 90분위 MAR {None if j['thr'] is None else round(j['thr'], 2)}"
              f" · 이웃 MAR {[n['mar'] for n in j['nbs']]} · 같은 MDD 지수+예금 CAGR {ref:+.1%}"
              f"\n      K1 {'O' if j['k1'] else 'X'}  K2 {'O' if j['k2'] else 'X'}  K3 {'O' if j['k3'] else 'X'}")
        if b0["trades"] < MIN_B0_TRADES or weak < MIN_WEAK_RATIO:
            undecidable.append(mk)
            print(f"      → 판정 불가 (B0 거래 {b0['trades']} · 약세 비율 {weak:.1%})")
        verdicts[mk] = j["k1"] and j["k2"] and j["k3"]
    ok = all(verdicts.values()) and not undecidable
    label = "합격" if ok else ("미합격(검증 불충분: 판정 불가 시장 " + ",".join(undecidable) + ")" if undecidable else "미합격")
    HOLDOUT_MARKER.write_text(f"{datetime.now().isoformat()} {cand} → {label} {verdicts}\n")
    print(f"\n홀드아웃 최종 판정: {label}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["regress", "dev", "holdout-check", "holdout"])
    ap.add_argument("--cand", help="holdout 전용 — 후보 이름 (2차 사전 등록은 G1-M2 하나)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.command == "regress":
        return regress()
    if a.command == "dev":
        return dev()
    if a.command == "holdout-check":
        return holdout_check()
    if not a.cand:
        raise SystemExit("--cand 가 필요하다")
    return holdout(a.cand, a.force)


if __name__ == "__main__":
    main()
