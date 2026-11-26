"""상대 모멘텀 — 순위·교체 계획·수량 계산 (순수 함수, DB·KIS 의존 없음)

전략: 월 1회, 시장별 종목끼리 6-1 모멘텀(종가(t−21봉) ÷ 종가(t−126봉) − 1) 상위 TOP_N 종목을 보유한다.
계속 상위인 종목은 거래하지 않고 이탈 종목만 팔고 새로 진입한 종목을 산다. 종목당 비중은 총자산의 MAX_POSITION_PCT.
연구·백테스트: scripts/research/relative_momentum.py (정의가 같음을 tests/test_momentum_ranking.py 의 패리티 테스트가 지킨다).

백테스트 대비 실전에서 일부러 다르게 한 것 — 모두 '매수가 조건 때문에 안 되는 일'을 줄이는 방향:
- 현금이 목표 수량에 모자라면 살 수 있는 만큼 산다 (목표의 PARTIAL_MIN_RATIO 이상일 때). 연구에서는 이 경우 통째로 건너뛰어
  평균 보유가 8~9종목에 그쳤고, 부분 매수 옵션으로 9.3~9.6종목·노출 96~98%가 됐다 (개발 구간 백테스트로 확인).
- 거래대금 한도(ADV_RATIO)를 넘으면 건너뛰지 않고 수량을 줄인다.
- 상위 TOP_N 중 살 수 없는 종목(거래 불가·스윙 충돌 등)은 차순위(BACKFILL_DEPTH 위까지)로 대체한다.

⚠️ 생존 편향: 유니버스가 DB 에 남은 종목이라 백테스트 절대 수치는 낙관적이다. 무손절·집중 보유라 낙폭이 크다(백테스트 MDD 23~50%).
"""
import math
from dataclasses import dataclass, field
from typing import Collection, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

LOOKBACK_BARS = 126        # 6개월 (M2)
SKIP_BARS = 21             # 최근 1개월 제외 (단기 반전 회피)
TOP_N = 10
MIN_UNIVERSE = 20          # 점수가 있는 종목이 이보다 적으면 순위를 믿지 않는다
MAX_POSITION_PCT = 0.095   # 종목당 목표 비중 (풀의 집중형 10종목과 같다)
PARTIAL_MIN_RATIO = 0.5    # 현금이 모자라면 목표 수량의 이 비율 이상일 때 살 수 있는 만큼 산다
ADV_RATIO = 0.05           # 일평균 거래대금 대비 주문 금액 상한 (풀의 MAX_ADV_RATIO 와 같다)
BACKFILL_DEPTH = 10        # 상위 TOP_N 중 살 수 없는 종목을 대체할 차순위 범위
COST_BUFFER = 0.01         # 수량 계산 시 수수료·호가 변동 여유



# 일봉 보정(분할·병합·오류 봉)은 공용 유틸 — 연구와 같은 정의 (패리티 테스트: tests/test_momentum_ranking.py)
from app.core.bars import SPLIT_MAX, SPLIT_TOLERANCE, adjust_splits, clean_bars, repair_bars, split_ratio  # noqa: E402,F401


# ==================== 점수·순위 ====================

@dataclass(frozen=True)
class Ranked:
    code: str
    score: float
    rank: int          # 1 = 가장 강함


def momentum_score(closes: Sequence[float], lookback: int = LOOKBACK_BARS, skip: int = SKIP_BARS) -> Optional[float]:
    """마지막 값이 최근 완성봉 종가인 오름차순 종가열의 모멘텀 — 종가(t−skip) ÷ 종가(t−lookback) − 1. 이력이 모자라면 None"""
    n = len(closes)
    if n < lookback + 1:
        return None
    recent, past = float(closes[n - 1 - skip]), float(closes[n - 1 - lookback])
    if not (past > 0 and recent > 0) or math.isnan(past) or math.isnan(recent):
        return None
    return recent / past - 1


def rank_universe(closes_by_code: Dict[str, Sequence[float]], lookback: int = LOOKBACK_BARS, skip: int = SKIP_BARS,
                  min_universe: int = MIN_UNIVERSE) -> List[Ranked]:
    """점수가 있는 종목을 점수 내림차순(동점은 코드순)으로 순위 매긴다. 점수 보유 종목이 min_universe 미만이면 빈 목록"""
    scored = []
    for code, closes in closes_by_code.items():
        s = momentum_score(closes, lookback, skip)
        if s is not None:
            scored.append((code, s))
    if len(scored) < min_universe:
        return []
    scored.sort(key=lambda x: (-x[1], x[0]))
    return [Ranked(code, s, i + 1) for i, (code, s) in enumerate(scored)]


# ==================== 교체 계획 ====================

@dataclass(frozen=True)
class RebalancePlan:
    target: List[str]                                    # 상위 TOP_N 종목 (순위순)
    sells: List[str]                                     # 보유 중 target 에서 이탈한 종목
    buys: List[str]                                      # target 중 아직 없는 종목 (순위순) — 이 순서로 산다
    backfill: List[str] = field(default_factory=list)    # buys 를 못 살 때 대체할 차순위 종목 (보유 아닌 것만, 순위순)


def plan_rebalance(held: Collection[str], ranked: Sequence[Ranked], top_n: int = TOP_N,
                   backfill_depth: int = BACKFILL_DEPTH) -> RebalancePlan:
    """보유 종목과 순위 → 교체 계획. 계속 상위인 종목은 거래하지 않는다. 순위가 비었으면 아무것도 바꾸지 않는다"""
    if not ranked:
        return RebalancePlan([], [], [], [])
    held_set = set(held)
    target = [r.code for r in ranked[:top_n]]
    target_set = set(target)
    return RebalancePlan(
        target=target,
        sells=sorted(held_set - target_set),
        buys=[c for c in target if c not in held_set],
        backfill=[r.code for r in ranked[top_n:top_n + backfill_depth] if r.code not in held_set],
    )


# ==================== 수량 ====================

def size_order(equity: float, cash: float, price: float, adv: Optional[float] = None,
               max_position_pct: float = MAX_POSITION_PCT, partial_min_ratio: float = PARTIAL_MIN_RATIO,
               adv_ratio: float = ADV_RATIO, cost_buffer: float = COST_BUFFER) -> int:
    """신규 매수 수량 — 목표(총자산 × 종목당 비중)를 거래대금 한도로 줄이고, 현금이 모자라면 목표의 일정 비율 이상일 때 살 수 있는 만큼

    0 을 돌려주면 이 종목은 살 수 없다 (가격이 목표 금액보다 비싸거나, 현금이 목표의 partial_min_ratio 에도 못 미침).
    """
    if equity <= 0 or price <= 0:
        return 0
    target_qty = int((equity * max_position_pct) // price)
    if adv is not None and adv > 0:
        target_qty = min(target_qty, int((adv * adv_ratio) // price))
    if target_qty <= 0:
        return 0
    affordable = int(max(cash, 0.0) // (price * (1 + cost_buffer)))
    if affordable >= target_qty:
        return target_qty
    return affordable if affordable >= max(1, int(target_qty * partial_min_ratio)) else 0
