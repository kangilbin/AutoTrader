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
    proximal_source: str      # "high"=OB 고가, "body"=몸통 상단(음봉 시가), "mean"=OB 중간값(50%)
    pivot_len: int
    ob_search_bars: int
    ob_max_age: int
    displacement_atr: float
    impulse_vol_ratio: float
    max_zone_atr: float
    # v2 품질 조건 (기본 꺼짐 — 검증을 통과한 것만 켠다, docs/01-plan/features/order-block-v2.plan.md 2장)
    require_fvg: bool = False         # 임펄스 구간에 공정가치갭(3봉 갭)
    require_sweep: bool = False       # OB(또는 직전 3봉)가 직전 스윙 저점을 깨고 내려갔다 (매도 유동성 스윕)
    require_discount: bool = False    # 근접선이 임펄스 구간(저점→고점)의 50% 이하


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


def confirmed_pivot_lows(low: np.ndarray, pivot_len: int) -> List[Tuple[int, int]]:
    """스윙 저점 [(pivot_idx, confirmed_idx)] — 좌우 pivot_len 봉보다 모두 낮을 때"""
    pivots = []
    for j in range(pivot_len, len(low) - pivot_len):
        left = low[j - pivot_len:j]
        right = low[j + 1:j + pivot_len + 1]
        if low[j] < left.min() and low[j] < right.min():
            pivots.append((j, j + pivot_len))
    return pivots


def _has_fvg(h: np.ndarray, l: np.ndarray, start: int, end: int) -> bool:
    """[start, end] 안에 상승 공정가치갭: low[i+1] > high[i−1] (가운데 봉 i가 갭을 남기고 지나감)"""
    return any(l[i + 1] > h[i - 1] for i in range(max(start, 1), end))


def _swept_low(l: np.ndarray, ob_idx: int, pivot_lows: List[Tuple[int, int]]) -> bool:
    """OB 이전에 확정된 가장 최근 스윙 저점을 OB(또는 직전 3봉)가 깨고 내려갔는가

    스윙 저점은 확인 창(OB−3 ~ OB) 밖, 그 이전에 있어야 한다 — 창 안의 저점은 자기 자신을 깰 수 없다.
    """
    prior = [p for p, conf in pivot_lows if conf <= ob_idx and p < ob_idx - 3]
    if not prior:
        return False
    swing_low = l[prior[-1]]
    window = l[max(0, ob_idx - 3):ob_idx + 1]
    return window.min() < swing_low


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
    pivot_lows = confirmed_pivot_lows(l, params.pivot_len) if params.require_sweep else []
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

        if params.require_fvg and not _has_fvg(h, l, ob_idx + 1, k):
            continue   # 임펄스에 갭이 없다
        if params.require_sweep and not _swept_low(l, ob_idx, pivot_lows):
            continue   # 유동성 스윕 없음

        if params.proximal_source == "high":
            proximal = h[ob_idx]
        elif params.proximal_source == "body":
            proximal = o[ob_idx]                       # 음봉 몸통 상단 = 시가
        else:
            proximal = (h[ob_idx] + l[ob_idx]) / 2     # 평균 임계값 (OB 50%)
        if params.require_discount:
            leg_low, leg_high = l[ob_idx:k + 1].min(), h[ob_idx:k + 1].max()
            if proximal > (leg_low + leg_high) / 2:
                continue   # 상승폭 위쪽 절반 — 할인 구간이 아니다

        used_ob.add(ob_idx)
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


def weekly_uptrend(df: pd.DataFrame, pivot_len: int = 2) -> np.ndarray:
    """일봉 행마다 "지난주까지 완성된 주봉 구조가 상승인가" — 주봉 BOS/CHoCH 상태

    주봉 종가가 직전 확정 스윙 고점을 넘으면(BOS) 상승, 직전 확정 스윙 저점을 깨면(CHoCH) 해제, 그 사이는 유지.
    "최근 두 고점·저점이 모두 상승"으로 정의하면 쉬지 않고 오르는 추세에선 스윙 고점이 안 생겨 하락처럼 판정된다.
    주봉은 금요일 마감 기준. 행 i는 자기 주(진행 중)를 쓰지 않고 그 이전 주까지만 본다 — 선견 방지.
    """
    dates = pd.to_datetime(df["STCK_BSOP_DATE"].astype(str))
    week = dates.dt.to_period("W-FRI")
    grouped = df.groupby(week.values)
    highs = grouped["STCK_HGPR"].max().to_numpy(dtype=float)
    lows = grouped["STCK_LWPR"].min().to_numpy(dtype=float)
    closes = grouped["STCK_CLPR"].last().to_numpy(dtype=float)
    weeks = grouped["STCK_HGPR"].max().index

    ph = confirmed_pivot_highs(highs, pivot_len)
    pl = confirmed_pivot_lows(lows, pivot_len)
    up = np.zeros(len(highs), dtype=bool)          # up[w]: 주 w 마감 시점의 상태
    state = False
    for w in range(len(highs)):
        last_high = next((highs[p] for p, conf in reversed(ph) if conf <= w), None)
        last_low = next((lows[p] for p, conf in reversed(pl) if conf <= w), None)
        if last_high is not None and closes[w] > last_high:
            state = True
        elif last_low is not None and closes[w] < last_low:
            state = False
        up[w] = state

    pos = pd.Index(weeks).get_indexer(week.values)
    return np.array([bool(up[x - 1]) if x >= 1 else False for x in pos])

