"""오더블럭 v2 검증 도구 — 기간 분할 백테스트 · 지표 · 무작위 진입 대조군 (연구용, 앱이 import하지 않는다)

계획: docs/01-plan/features/order-block-v2.plan.md (2장 검증 체계)

실행 예:
  uv run python scripts/research/order_block_research.py baseline              # 학습·검증 기간
  uv run python scripts/research/order_block_research.py random --runs 100     # 무작위 대조군
  uv run python scripts/research/order_block_research.py ablation              # 조건 단독 추가 / 전체 / 전체−하나
  uv run python scripts/research/order_block_research.py baseline --final      # 최종 확인 (마지막에 한 번만)

기간은 결과를 보기 전에 고정한다 (데이터: 2019-10 ~ 2026-10, 지표 워밍업 약 6개월):
  학습  2020-05-01 ~ 2022-12-31  (2022 하락장 포함)
  검증  2023-01-01 ~ 2024-12-31
  최종  2025-01-01 ~ 2026-10-23  (--final 없이는 돌지 않는다)
"""
import argparse
import asyncio
import json
import math
import random
import statistics as st
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.common.database import Database  # noqa: E402
from app.domain.stock.service import StockService  # noqa: E402
from app.domain.swing.backtest.strategies.base_strategy import fill_buy  # noqa: E402
from app.domain.swing.backtest.strategies.order_block_backtest_strategy import OrderBlockBacktestStrategy  # noqa: E402

PERIODS = {
    "train": ("2020-05-01", "2022-12-31"),
    "valid": ("2023-01-01", "2024-12-31"),
    "final": ("2025-01-01", "2026-10-23"),
}
MIN_BARS = 300        # 이보다 짧은 종목은 제외 (워밍업 + 평가에 부족)
INIT_AMOUNT = 10_000_000


# ==================== 데이터 ====================

async def load_universe(since: str = "2019-01-01") -> Dict[tuple, pd.DataFrame]:
    """DB의 일봉 전체 (MRKT_CODE, ST_CODE) → DataFrame. 지수 ETF는 제외하지 않는다 (필터용으로도 쓴다)"""
    db = await Database.get_session()
    try:
        codes = (await db.execute(text(
            "SELECT MRKT_CODE, ST_CODE, COUNT(*) FROM STOCK_DAY_HISTORY GROUP BY MRKT_CODE, ST_CODE"))).all()
        data = {}
        for m, c, n in codes:
            if n < MIN_BARS:
                continue
            rows = await StockService(db).get_stock_history(m, c, datetime.fromisoformat(since))
            data[(m, c)] = repair_bars(pd.DataFrame(rows).sort_values("STCK_BSOP_DATE").reset_index(drop=True))
        return data
    finally:
        await db.close()


def repair_bars(df: pd.DataFrame) -> pd.DataFrame:
    """명백한 데이터 오류 봉 보정 — 고·저가가 시가·종가에서 터무니없이 벗어나면 시가·종가 범위로 자른다

    예: VZ 2026-01-08 종가 40.57, 저가 10.60 (KIS 원본 오류). 그대로 두면 가짜 손절이 난다.
    연구용 로드에만 적용하고 DB·서비스 데이터는 건드리지 않는다.
    """
    df = df.copy()
    for col in ("STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR"):
        df[col] = df[col].astype(float)
    body_low = df[["STCK_OPRC", "STCK_CLPR"]].min(axis=1)
    body_high = df[["STCK_OPRC", "STCK_CLPR"]].max(axis=1)
    bad_low = df["STCK_LWPR"] < body_low * 0.5
    bad_high = df["STCK_HGPR"] > body_high * 2.0
    df.loc[bad_low, "STCK_LWPR"] = body_low[bad_low]
    df.loc[bad_high, "STCK_HGPR"] = body_high[bad_high]
    return df


INDEX_CODES = {("J", "069500"), ("AMS", "SPY"), ("NAS", "QQQ")}
MARKET_INDEX = {"KR": ("J", "069500"), "US": ("AMS", "SPY")}     # 시장 국면 판정용 지수 ETF
MARKET_EMA_PERIOD = 100


def market_regimes(data: Dict[tuple, pd.DataFrame]) -> Dict[str, pd.Series]:
    """시장별 국면 (날짜 → 지수 종가 > 지수 EMA100). 전략은 전일 날짜로 asof 조회한다"""
    out = {}
    for mk, key in MARKET_INDEX.items():
        df = data.get(key)
        if df is None:
            continue
        close = df["STCK_CLPR"].astype(float)
        ema = close.ewm(span=MARKET_EMA_PERIOD, adjust=False).mean()
        idx = pd.to_datetime(df["STCK_BSOP_DATE"].astype(str))
        flag = pd.Series((close > ema).to_numpy(), index=idx)
        flag.iloc[:MARKET_EMA_PERIOD] = False                  # EMA 워밍업 구간은 판정 불가 → 매수 안 함
        out[mk] = flag
    return out


def tradable(data: Dict[tuple, pd.DataFrame]) -> Dict[tuple, pd.DataFrame]:
    return {k: v for k, v in data.items() if k not in INDEX_CODES}


# ==================== 실행 ====================

def run_period(strategy: OrderBlockBacktestStrategy, data: Dict[tuple, pd.DataFrame], period: str,
               regimes: Optional[Dict[str, pd.Series]] = None) -> List[Dict]:
    """기간 하나를 전 종목에 돌려 사이클 목록 (종목·시장 표시 포함)"""
    start, end = PERIODS[period]
    end_key = end.replace("-", "")
    cycles = []
    for (m, c), df in data.items():
        sub = df[df["STCK_BSOP_DATE"] <= end_key]
        if len(sub) < MIN_BARS // 2 or sub["STCK_BSOP_DATE"].iloc[-1] < start.replace("-", ""):
            continue
        mk = "KR" if m == "J" else "US"
        result = strategy.compute(sub.copy(), {"st_code": c, "mrkt_code": m, "swing_type": "O",
                                               "init_amount": INIT_AMOUNT, "eval_start": start,
                                               "market_regime": (regimes or {}).get(mk)})
        for cy in result["strategy_stats"]["cycles"]:
            cycles.append({**cy, "mrkt": mk, "code": c})
    return cycles


# ==================== 지표 ====================

def metrics(cycles: List[Dict], years: float) -> Dict:
    """매매 단위 지표 — 순 R 기준 (비용·슬리피지 반영)"""
    r = [c["r_net"] for c in cycles]
    if not r:
        return {"n": 0}
    n = len(r)
    mean = st.mean(r)
    sd = st.stdev(r) if n > 1 else float("nan")
    se = sd / math.sqrt(n) if n > 1 else float("nan")
    wins = [x for x in r if x > 0]
    losses = [-x for x in r if x < 0]
    # 낙폭: 청산일 순으로 R 누적 (포지션 크기가 R 단위라 계좌 위험의 근사)
    equity, peak, mdd = 0.0, 0.0, 0.0
    for c in sorted(cycles, key=lambda c: c["exit_date"]):
        equity += c["r_net"]
        peak = max(peak, equity)
        mdd = max(mdd, peak - equity)
    return {
        "n": n, "per_year": round(n / years, 1),
        "mean_r": round(mean, 3), "median_r": round(st.median(r), 3),
        "se": round(se, 3), "t": round(mean / se, 2) if se and se > 0 else None,
        "win_rate": round(len(wins) / n, 3),
        "profit_factor": round(sum(wins) / sum(losses), 2) if losses else None,
        "total_r": round(sum(r), 1), "mdd_r": round(mdd, 1),
    }


def period_years(period: str) -> float:
    start, end = PERIODS[period]
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days / 365.25


def report(label: str, cycles: List[Dict], period: str) -> Dict:
    years = period_years(period)
    out = {"all": metrics(cycles, years)}
    for mk in ("KR", "US"):
        out[mk] = metrics([c for c in cycles if c["mrkt"] == mk], years)
    print(f"[{label}] {period}: " + json.dumps(out, ensure_ascii=False))
    return out


# ==================== 무작위 진입 대조군 ====================

class RandomEntry(OrderBlockBacktestStrategy):
    """진입만 무작위 — 청산·사이징·쿨다운은 오더블럭과 같다

    매수 대기 봉마다 확률 p로 시가에 진입, 손절 = 진입가 − k×ATR (k는 실제 오더블럭 진입의 R/ATR 중앙값).
    같은 청산 규칙에서 오더블럭 진입이 이 분포의 상위 5% 안에 들어야 "진입에 가치 있음".
    """
    P = 0.01
    K = 1.5
    SEED = 0

    def __init__(self):
        super().__init__()
        self.rng = random.Random(self.SEED)

    def _find_entry(self, ctx, i, row, prev, atr, used, stats) -> Optional[Dict]:
        if atr <= 0 or self.rng.random() >= self.P:
            return None
        price = fill_buy(float(row["STCK_OPRC"]), ctx["mrkt_code"])
        return {"price": price, "stop": price - self.K * atr, "reasons": ["무작위"], "key": ("rnd", i)}


def r_over_atr(data, period) -> float:
    """실제 오더블럭 진입의 손절폭(R)/ATR 중앙값 — 대조군 손절폭을 같은 크기로 맞춘다"""
    ratios = []

    class Probe(OrderBlockBacktestStrategy):
        def _find_entry(self, ctx, i, row, prev, atr, used, stats):
            found = super()._find_entry(ctx, i, row, prev, atr, used, stats)
            if found and atr > 0:
                ratios.append((found["price"] - found["stop"]) / atr)
            return found

    run_period(Probe(), data, period)
    return st.median(ratios) if ratios else 1.5


def random_baseline(data, period: str, runs: int, target_n: int, k: float) -> List[float]:
    """대조군 평균 순 R 분포 — 진입 수를 오더블럭과 비슷하게 맞춘다 (p를 한 번 보정)"""
    def run_once(p, seed):
        cls = type("R", (RandomEntry,), {"P": p, "K": k, "SEED": seed})
        return run_period(cls(), data, period)

    p = 0.01
    probe = run_once(p, 10_000)
    if probe:
        p = min(0.5, p * target_n / len(probe))
    means = []
    for seed in range(runs):
        cycles = run_once(p, seed)
        if cycles:
            means.append(st.mean(c["r_net"] for c in cycles))
    return means


# ==================== Ablation ====================

# 일봉으로 검증 가능한 v2 조건 (계획서 2.4-3) — 값은 계획서 초기값
CONDITIONS = {
    "1-1 시장국면": {"MARKET_FILTER": True},
    "1-2 주봉구조": {"WEEKLY_STRUCTURE": True},
    "2-2 FVG": {"REQUIRE_FVG": True},
    "2-3 스윕": {"REQUIRE_SWEEP": True},
    "2-4 할인": {"REQUIRE_DISCOUNT": True},
    "2-7 근접선50%": {"PROXIMAL_SOURCE": "mean"},
    "3-2 저점반등": {"REBOUND_ATR": 0.3},
    "4-2 직전고점목표": {"TARGET_MODE": "swing"},
    "4-4 시간청산": {"TIME_STOP_DAYS": 10},
}


def variant(overrides: Dict) -> OrderBlockBacktestStrategy:
    return type("V", (OrderBlockBacktestStrategy,), dict(overrides))()


def ablation(data, regimes, periods) -> Dict:
    """v1 / 단독 추가 / 전체 / 전체 − 하나 — 기간별 지표"""
    all_on = {k: v for ov in CONDITIONS.values() for k, v in ov.items()}
    configs = {"v1": {}}
    configs.update({f"+{name}": ov for name, ov in CONDITIONS.items()})
    configs["전체"] = all_on
    for name, ov in CONDITIONS.items():
        configs[f"전체−{name}"] = {k: v for k, v in all_on.items() if k not in ov}

    out = {}
    for label, ov in configs.items():
        out[label] = {}
        for period in periods:
            cycles = run_period(variant(ov), data, period, regimes)
            out[label][period] = metrics(cycles, period_years(period))
        row = " | ".join(f"{p} n={out[label][p].get('n')} R={out[label][p].get('mean_r')} t={out[label][p].get('t')}"
                         for p in periods)
        print(f"[ablation] {label:16} {row}", flush=True)
    return out


# ==================== CLI ====================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["baseline", "random", "ablation"])
    ap.add_argument("--final", action="store_true", help="최종 확인 기간까지 (마지막에 한 번만)")
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    periods = ["train", "valid"] + (["final"] if args.final else [])
    universe = asyncio.run(load_universe())
    regimes = market_regimes(universe)
    data = tradable(universe)
    print(f"종목 {len(data)} (KR {sum(1 for k in data if k[0] == 'J')}, US {sum(1 for k in data if k[0] != 'J')}), "
          f"시장 국면 {list(regimes)}")

    if args.mode == "ablation":
        results = ablation(data, regimes, periods)
        if args.out:
            Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2))
        return

    results = {}
    for period in periods:
        real = run_period(OrderBlockBacktestStrategy(), data, period, regimes)
        results[period] = {"order_block": report("오더블럭", real, period)}
        if args.mode == "random":
            k = r_over_atr(data, period)
            means = random_baseline(data, period, args.runs, len(real), k)
            ob_mean = results[period]["order_block"]["all"].get("mean_r")
            pct = sum(1 for m in means if m < ob_mean) / len(means) if means and ob_mean is not None else None
            results[period]["random"] = {
                "k_r_over_atr": round(k, 2), "runs": len(means),
                "mean_of_means": round(st.mean(means), 3) if means else None,
                "p95": round(sorted(means)[int(0.95 * (len(means) - 1))], 3) if means else None,
                "order_block_percentile": round(pct, 3) if pct is not None else None,
            }
            print(f"[대조군] {period}: " + json.dumps(results[period]["random"], ensure_ascii=False))

    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
