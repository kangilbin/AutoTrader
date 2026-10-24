"""
오더블럭(Order Block) 탐지 — 실전 워밍업·백테스트 공용 순수 함수

상승 구조 돌파(BOS)를 만든 임펄스 직전의 마지막 음봉을 오더블럭으로 본다.
규칙 정의: docs/01-plan/features/order-block-strategy.plan.md (2장),
          docs/02-design/features/order-block-strategy.design.md (3장)

선견 방지 계약:
- 스윙 고점은 좌우 pivot_len 봉이 모두 완성돼야 확정된다 (confirmed_idx = pivot_idx + pivot_len).
- BOS 봉 k는 k-1 이하에서 확정된 고점만 기준으로 삼고, 품질 지표도 k의 값만 쓴다.
- active_order_block(as_of)는 as_of 행까지만 읽는다 → as_of 이후 데이터는 결과에 영향이 없다.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class OBParams:
    """탐지 파라미터 (BaseOrderBlockStrategy.ob_params()가 상수로 채운다)"""
    proximal_source: str      # "high"=OB 고가, "body"=몸통 상단(음봉 시가)
    pivot_len: int
    ob_search_bars: int
    ob_max_age: int
    displacement_atr: float
    impulse_vol_ratio: float
    max_zone_atr: float


@dataclass(frozen=True)
class OrderBlock:
    ob_idx: int        # OB 캔들 행 위치
    bos_idx: int       # BOS 봉 행 위치 — 이 봉이 완성돼야 OB가 존재한다
    ob_date: str       # YYYYMMDD (캐시·로그 식별자)
    bos_date: str
    proximal: float    # 근접선 (매수 기준선)
    distal: float      # 원격선 = OB 캔들 저가 (손절 기준)


def confirmed_pivot_highs(high: np.ndarray, pivot_len: int) -> List[Tuple[int, int]]:
    """스윙 고점 [(pivot_idx, confirmed_idx)] — 좌우 pivot_len 봉보다 모두 높을 때 (동률은 제외)"""
    pivots = []
    for j in range(pivot_len, len(high) - pivot_len):
        left = high[j - pivot_len:j]
        right = high[j + 1:j + pivot_len + 1]
        if high[j] > left.max() and high[j] > right.max():
            pivots.append((j, j + pivot_len))
    return pivots


def _date_str(value) -> str:
    return value.strftime("%Y%m%d") if hasattr(value, "strftime") else str(value)


def detect_order_blocks(df: pd.DataFrame, params: OBParams) -> List[OrderBlock]:
    """품질 조건을 통과한 OB 후보 전부 (bos_idx 오름차순)

    필요 컬럼: STCK_BSOP_DATE, STCK_OPRC, STCK_HGPR, STCK_LWPR, STCK_CLPR, ACML_VOL,
              atr, avg_vol20, ema120
    """
    o = df["STCK_OPRC"].to_numpy(dtype=float)
    h = df["STCK_HGPR"].to_numpy(dtype=float)
    l = df["STCK_LWPR"].to_numpy(dtype=float)
    c = df["STCK_CLPR"].to_numpy(dtype=float)
    v = df["ACML_VOL"].to_numpy(dtype=float)
    atr = df["atr"].to_numpy(dtype=float)
    avg_vol = df["avg_vol20"].to_numpy(dtype=float)
    trend_ema = df["ema120"].to_numpy(dtype=float)
    dates = df["STCK_BSOP_DATE"].tolist()

    pivots = confirmed_pivot_highs(h, params.pivot_len)
    next_pivot = 0
    current = None           # k-1 이하에서 확정된 가장 최근 고점
    broken = set()           # 이미 BOS가 난 고점 (고점당 1회)
    used_ob = set()          # 같은 음봉을 가리키는 BOS는 먼저 생긴 것만
    blocks: List[OrderBlock] = []

    for k in range(len(df)):
        while next_pivot < len(pivots) and pivots[next_pivot][1] <= k - 1:
            current = pivots[next_pivot]
            next_pivot += 1
        if current is None or current[0] in broken or not c[k] > h[current[0]]:
            continue
        broken.add(current[0])

        ob_idx = next((j for j in range(k - 1, max(k - 1 - params.ob_search_bars, -1), -1)
                       if c[j] < o[j]), None)
        if ob_idx is None or ob_idx in used_ob:
            continue

        a, av, ema = atr[k], avg_vol[k], trend_ema[k]
        if np.isnan(a) or np.isnan(av) or np.isnan(ema) or a <= 0:
            continue   # 지표 워밍업 구간
        if c[k] - l[ob_idx] < params.displacement_atr * a:
            continue   # 변위 부족
        if v[ob_idx + 1:k + 1].max() < params.impulse_vol_ratio * av:
            continue   # 임펄스 거래량 부족
        if h[ob_idx] - l[ob_idx] > params.max_zone_atr * a:
            continue   # 존이 너무 넓다 (R이 커진다)
        if not c[k] > ema:
            continue   # 장기 하락 추세

        used_ob.add(ob_idx)
        proximal = h[ob_idx] if params.proximal_source == "high" else o[ob_idx]
        blocks.append(OrderBlock(
            ob_idx=ob_idx, bos_idx=k,
            ob_date=_date_str(dates[ob_idx]), bos_date=_date_str(dates[k]),
            proximal=float(proximal), distal=float(l[ob_idx]),
        ))
    return blocks


def active_order_block(df: pd.DataFrame, candidates: List[OrderBlock],
                       as_of: int, params: OBParams) -> Optional[OrderBlock]:
    """as_of 행(마지막 완성봉)까지의 정보로 활성 OB 1개 — 가장 최근 BOS

    활성 = BOS 후 OB_MAX_AGE 이내 + BOS 다음 봉~as_of 동안 저가가 근접선에 닿은 적 없음.
    BOS가 as_of 당일이면 확인 구간이 비어 활성이다 (다음 날 첫 리테스트 가능).
    종가 붕괴(원격선 아래 마감)는 따로 보지 않는다 — 원격선 ≤ 근접선이라 붕괴는 항상 터치를 포함한다.
    """
    low = df["STCK_LWPR"].to_numpy(dtype=float)
    active = None
    for ob in candidates:
        if ob.bos_idx > as_of:
            break
        if as_of - ob.bos_idx > params.ob_max_age:
            continue
        if ob.bos_idx < as_of and low[ob.bos_idx + 1:as_of + 1].min() <= ob.proximal:
            continue   # 이미 터치됨 (첫 리테스트만 매매)
        active = ob
    return active
