"""일목균형표 전략 공통 규칙 — 실전(IchimokuStrategy)과 백테스트(IchimokuBacktestStrategy)가 **같은 정의**를 쓴다

단일 EMA 가 BaseSingleEMAStrategy 를 실전·백테스트가 함께 상속하는 것과 같은 구조다. 판정 로직을 이 모듈 한 곳에만 두어
"일목으로 설정하면 일목으로 백테스트된다"는 약속이 코드 수준에서 어긋나지 않게 한다.

규칙 (SWING_TYPE='I', 표준 기간 9/26/52 — 튜닝하지 않는다):
  진입  구름 위(종가 > 구름 상단) + 전환선 > 기준선인 상태가 **최근 RECENT_DAYS 거래일 안에 성립해 지금도 유지** 중
        (막 성립한 상태만 — 오래 유지돼 온 상태에는 새로 들어가지 않는다)
  청산  종가가 구름 하단 아래로 마감
  없음  부분익절·OBV 쿨다운·종가 확인 — 청산 뒤 조건이 다시 맞으면 바로 다시 들어갈 수 있다
  사이징 손절선  구름 하단 (풀 사이징이 '손절선까지의 거리'로 수량을 정한다)
신호는 **완성된 봉** 기준이다 — t 봉 종가로 판정하고 t+1 봉 시가에 체결한다 (실전은 전일 완성봉으로 판정해 오늘 장 시작 뒤 첫 배치에서 주문).

근거와 한계 (scripts/research/ichimoku_popular.py 워크포워드, 후보명 B0-X2):
  훈련(2020-05~2023-06)에서 15개 후보 중 1위로 뽑혀 동결, 검증(2023-07~2026-10)에서 한국 CAGR 17.5%·MDD 27.5%·MAR 0.64,
  미국 4.3%·25.6%·0.17 로 **두 시장 모두 플러스**였지만 같은 낙폭의 "지수 + 예금" 선에는 못 미쳤다 (사전 기준 미통과).
  추세가 없는 구간에서는 구름 근처를 오가며 휩쏘로 잃는다. 사용자가 모의에서 확인하겠다고 해 구현했다.
"""
from typing import Dict, Optional

import numpy as np
import pandas as pd


class BaseIchimokuStrategy:
    TENKAN_PERIOD = 9
    KIJUN_PERIOD = 26
    SENKOU_B_PERIOD = 52
    SENKOU_SHIFT = 26
    RECENT_DAYS = 5

    # 구름(선행스팬 B 52봉 + 26봉 시프트)이 처음 계산되는 봉 + 최근 상태 판정에 필요한 봉
    MIN_BARS = SENKOU_B_PERIOD + SENKOU_SHIFT + RECENT_DAYS + 1

    @classmethod
    def compute_frame(cls, df: pd.DataFrame) -> pd.DataFrame:
        """일봉 → 지표·신호 프레임 (날짜 오름차순, 인덱스는 df 와 같은 위치). 모든 값은 그 봉의 종가 시점까지의 데이터만 쓴다

        컬럼: tenkan, kijun, senkou_a, senkou_b, cloud_top, cloud_bot, state(구름 위 + 전환선>기준선), entry, exit
        선행스팬은 26봉 전에 계산된 값이다 (그 봉에 이미 알려진 구름) — 선견이 없다.
        """
        h = df["STCK_HGPR"].astype(float).reset_index(drop=True)
        lo = df["STCK_LWPR"].astype(float).reset_index(drop=True)
        c = df["STCK_CLPR"].astype(float).reset_index(drop=True)

        tenkan = (h.rolling(cls.TENKAN_PERIOD).max() + lo.rolling(cls.TENKAN_PERIOD).min()) / 2
        kijun = (h.rolling(cls.KIJUN_PERIOD).max() + lo.rolling(cls.KIJUN_PERIOD).min()) / 2
        sa = ((tenkan + kijun) / 2).shift(cls.SENKOU_SHIFT)
        sb = ((h.rolling(cls.SENKOU_B_PERIOD).max() + lo.rolling(cls.SENKOU_B_PERIOD).min()) / 2).shift(cls.SENKOU_SHIFT)
        top, bot = np.maximum(sa, sb), np.minimum(sa, sb)

        state = (c > top) & (tenkan > kijun)
        prev_all = state.astype(float).shift(1).rolling(cls.RECENT_DAYS).sum() == cls.RECENT_DAYS
        out = pd.DataFrame({
            "tenkan": tenkan, "kijun": kijun, "senkou_a": sa, "senkou_b": sb, "cloud_top": top, "cloud_bot": bot,
            "state": state, "entry": state & ~prev_all, "exit": c < bot,
        })
        out.index = df.index
        return out

    @classmethod
    def snapshot(cls, df: pd.DataFrame) -> Optional[Dict]:
        """마지막 완성봉 기준 신호 요약 — 실전 지표 캐시(Redis)에 들어가는 값. 봉이 모자라거나 구름을 못 구하면 None"""
        if df is None or len(df) < cls.MIN_BARS:
            return None
        frame = cls.compute_frame(df.sort_values("STCK_BSOP_DATE") if "STCK_BSOP_DATE" in df.columns else df)
        last = frame.iloc[-1]
        if pd.isna(last["cloud_bot"]) or pd.isna(last["cloud_top"]) or pd.isna(last["tenkan"]) or pd.isna(last["kijun"]):
            return None
        return {
            "ichi_tenkan": float(last["tenkan"]), "ichi_kijun": float(last["kijun"]),
            "ichi_top": float(last["cloud_top"]), "ichi_bot": float(last["cloud_bot"]),
            "ichi_state": bool(last["state"]), "ichi_entry": bool(last["entry"]), "ichi_exit": bool(last["exit"]),
        }
