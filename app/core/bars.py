"""일봉 보정 유틸 — 오류 봉 보정과 미보정 액면분할·병합의 소급 보정 (순수 함수)

DB 일봉에는 보정되지 않은 분할·병합이 남아 있는 종목이 있다 (AMZN 20:1, NFLX 10:1, GE 1:8 병합 등).
보정하지 않으면 지표·순위가 가짜 급등락을 본다. DB 자체는 건드리지 않는다.
연구(scripts/research/portfolio_sim.py)의 같은 이름 함수와 정의가 같다 — 패리티 테스트가 지킨다 (tests/test_momentum_ranking.py).
사용처: 상대 모멘텀 순위(domain/momentum), 일목균형표 지표 캐시(domain/swing/service.py)
"""
from typing import Optional

import numpy as np
import pandas as pd

SPLIT_MAX = 50             # 분할·병합 비율 상한 (k:1 / 1:k, 정수)
SPLIT_TOLERANCE = 0.05     # 시가 갭 비율이 가장 가까운 정수 비율에서 이 비율 이내일 때만 분할로 본다


def repair_bars(df: pd.DataFrame) -> pd.DataFrame:
    """명백한 데이터 오류 봉 보정 — 고·저가가 시가·종가에서 터무니없이 벗어나면 시가·종가 범위로 자른다 (KIS 원본 오류 대비)"""
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


def split_ratio(prev_close: float, open_price: float) -> Optional[float]:
    """분할일 시가 ÷ 전일 종가가 분할(1/k)·병합(k) 비율이면 그 비율, 아니면 None (실제 폭락을 분할로 오인하지 않는다)"""
    if prev_close <= 0 or open_price <= 0:
        return None
    r = open_price / prev_close
    k = round(1 / r) if r < 1 else round(r)
    if k < 2 or k > SPLIT_MAX:
        return None
    target = 1 / k if r < 1 else float(k)
    return target if abs(r - target) / target <= SPLIT_TOLERANCE else None


def adjust_splits(df: pd.DataFrame) -> pd.DataFrame:
    """미보정 액면분할·병합을 소급 보정한다 — 분할일 이전 가격에 비율을 곱하고 거래량을 나눈다

    DB 일봉에 분할이 보정되지 않은 채 남아 있는 종목이 있다(AMZN 20:1, NFLX 10:1, GE 1:8 병합 등).
    보정하지 않으면 병합 종목이 가짜 모멘텀 1등이, 분할 종목이 가짜 −90%가 된다. DB 자체는 건드리지 않는다.
    거래량이 없는 봉은 분할인지 판단할 수 없어 건드리지 않는다.
    """
    df = df.copy()
    close = df["STCK_CLPR"].astype(float).to_numpy()
    open_ = df["STCK_OPRC"].astype(float).to_numpy()
    vol = df["ACML_VOL"].astype(float).to_numpy()
    n = len(df)
    if n == 0:
        return df
    mult = np.ones(n)
    cum = 1.0
    for i in range(n - 1, 0, -1):            # 최근 → 과거: 분할일 이전 봉에 그 비율을 누적해서 곱한다
        if not (vol[i] > 0 and vol[i - 1] > 0):
            mult[i - 1] = cum
            continue
        r = split_ratio(close[i - 1], open_[i])
        if r is not None:
            cum *= r
        mult[i - 1] = cum
    mult[n - 1] = 1.0
    for col in ("STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR"):
        df[col] = df[col].astype(float) * mult
    df["ACML_VOL"] = df["ACML_VOL"].astype(float) / mult
    return df


def clean_bars(df: pd.DataFrame) -> pd.DataFrame:
    """날짜 오름차순 정렬 + 오류 봉 보정 + 분할 보정 (연구의 load_universe 와 같은 순서)"""
    return adjust_splits(repair_bars(df.sort_values("STCK_BSOP_DATE").reset_index(drop=True)))
