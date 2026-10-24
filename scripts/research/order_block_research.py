"""오더블럭 v2 검증 도구 — 기간 분할 백테스트 · 지표 · 무작위 진입 대조군 (연구용, 앱이 import하지 않는다)

계획: docs/01-plan/features/order-block-v2.plan.md (2장 검증 체계)

실행 예:
  uv run python scripts/research/order_block_research.py baseline              # 학습·검증 기간
  uv run python scripts/research/order_block_research.py random --runs 100     # 무작위 대조군
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
            data[(m, c)] = pd.DataFrame(rows).sort_values("STCK_BSOP_DATE").reset_index(drop=True)
        return data
    finally:
        await db.close()


INDEX_CODES = {("J", "069500"), ("AMS", "SPY"), ("NAS", "QQQ")}


def tradable(data: Dict[tuple, pd.DataFrame]) -> Dict[tuple, pd.DataFrame]:
    return {k: v for k, v in data.items() if k not in INDEX_CODES}


# ==================== 실행 ====================

def run_period(strategy: OrderBlockBacktestStrategy, data: Dict[tuple, pd.DataFrame], period: str) -> List[Dict]:
    """기간 하나를 전 종목에 돌려 사이클 목록 (종목·시장 표시 포함)"""
    start, end = PERIODS[period]
    end_key = end.replace("-", "")
    cycles = []
    for (m, c), df in data.items():
        sub = df[df["STCK_BSOP_DATE"] <= end_key]
        if len(sub) < MIN_BARS // 2 or sub["STCK_BSOP_DATE"].iloc[-1] < start.replace("-", ""):
            continue
        result = strategy.compute(sub.copy(), {"st_code": c, "mrkt_code": m, "swing_type": "O",
                                               "init_amount": INIT_AMOUNT, "eval_start": start})
        for cy in result["strategy_stats"]["cycles"]:
            cycles.append({**cy, "mrkt": "KR" if m == "J" else "US", "code": c})
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


# ==================== CLI ====================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["baseline", "random"])
    ap.add_argument("--final", action="store_true", help="최종 확인 기간까지 (마지막에 한 번만)")
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    periods = ["train", "valid"] + (["final"] if args.final else [])
    data = tradable(asyncio.run(load_universe()))
    print(f"종목 {len(data)} (KR {sum(1 for k in data if k[0] == 'J')}, US {sum(1 for k in data if k[0] != 'J')})")

    results = {}
    for period in periods:
        real = run_period(OrderBlockBacktestStrategy(), data, period)
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
