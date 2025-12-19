"""
TYPE B - Momentum Chasing Strategy (모멘텀 추격 전략)

Entry Conditions (all 3 must be met):
1. 5% 이상 급등 (당일 시가 대비)
2. 폭발적 거래량 (전일 대비 200% 이상)
3. 현재가가 당일 고가 95% 이상 (상단 근처 거래)

Risk Management:
- Same as TYPE A (동일한 손절/익절 로직 적용)
"""
import pandas as pd
import talib as ta
from typing import Dict, Optional
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


class TypeBStrategy:
    """TYPE B - 모멘텀 추격 전략"""

    # 전략 파라미터
    SURGE_THRESHOLD = 0.05  # 5% 급등 기준
    VOLUME_EXPLOSION_RATIO = 2.0  # 전일 대비 200% 거래량
    HIGH_PROXIMITY_RATIO = 0.95  # 당일 고가 대비 95% 이상

    # Risk Management (TYPE A와 동일)
    EMA_PERIOD = 20
    STOP_LOSS_FIXED = -0.03  # -3%
    STOP_LOSS_EMA_DEVIATION = -0.05  # 20일 이평선 -5% 이탈
    PROFIT_TARGET_FIRST = 0.05  # +5%
    PROFIT_TARGET_SECOND = 0.15  # +15%
    PROFIT_RATIO_FIRST = 0.3  # 30% 익절
    PROFIT_RATIO_SECOND = 0.7  # 70% 익절

    @classmethod
    def analyze(
        cls,
        df: pd.DataFrame,
        current_price: Decimal,
        entry_price: Optional[Decimal] = None,
        current_signal: int = 0
    ) -> Dict:
        """
        TYPE B 전략 분석

        Args:
            df: 주가 데이터 (OHLCV)
            current_price: 현재가
            entry_price: 진입가 (손익 계산용)
            current_signal: 현재 신호 상태

        Returns:
            {
                "signal": "buy" | "sell" | "hold",
                "strength": "strong" | "weak" | None,
                "score": int (0-3),
                "conditions": {
                    "surge_detected": bool,
                    "volume_explosion": bool,
                    "near_high": bool
                },
                "indicators": {
                    "price_change_rate": float,
                    "volume_ratio": float,
                    "high_proximity": float,
                    "ema_20": float,
                    "profit_rate": float (if entry_price)
                },
                "reason": str
            }
        """
        # 데이터 정렬 및 숫자형 변환
        if "STCK_BSOP_DATE" in df.columns:
            df = df.sort_values("STCK_BSOP_DATE").reset_index(drop=True)

        for col in ["STCK_OPRC", "STCK_HGPR", "STCK_CLPR", "ACML_VOL"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # 최소 데이터 확인
        if len(df) < 2:
            return {
                "signal": "hold",
                "strength": None,
                "score": 0,
                "conditions": {},
                "indicators": {},
                "reason": "데이터 부족 (최소 2일 필요)"
            }

        # 당일 데이터
        today_open = df["STCK_OPRC"].iloc[-1]
        today_high = df["STCK_HGPR"].iloc[-1]
        today_close = df["STCK_CLPR"].iloc[-1]
        today_volume = df["ACML_VOL"].iloc[-1]

        # 전일 데이터
        prev_volume = df["ACML_VOL"].iloc[-2]

        # === 지표 계산 ===
        # 1. 급등 여부 (시가 대비)
        price_change_rate = (float(current_price) - float(today_open)) / float(today_open) if today_open > 0 else 0
        surge_detected = price_change_rate >= cls.SURGE_THRESHOLD

        # 2. 거래량 폭발
        volume_ratio = (today_volume / prev_volume) if prev_volume > 0 else 0
        volume_explosion = volume_ratio >= cls.VOLUME_EXPLOSION_RATIO

        # 3. 당일 고가 근처 거래
        high_proximity = (float(current_price) / float(today_high)) if today_high > 0 else 0
        near_high = high_proximity >= cls.HIGH_PROXIMITY_RATIO

        # 점수 계산
        score = sum([surge_detected, volume_explosion, near_high])

        conditions = {
            "surge_detected": surge_detected,
            "volume_explosion": volume_explosion,
            "near_high": near_high
        }

        indicators = {
            "price_change_rate": float(price_change_rate),
            "volume_ratio": float(volume_ratio),
            "high_proximity": float(high_proximity),
        }

        # 20일 EMA 계산 (손절 조건용)
        if len(df) >= cls.EMA_PERIOD:
            close = df["STCK_CLPR"].values
            ema_20 = pd.Series(ta.EMA(close, timeperiod=cls.EMA_PERIOD), index=df.index)
            if not pd.isna(ema_20.iloc[-1]):
                indicators["ema_20"] = float(ema_20.iloc[-1])

        # === Signal Generation ===

        # Exit Signals (포지션 있을 때)
        if entry_price:
            profit_rate = (float(current_price) - float(entry_price)) / float(entry_price)
            indicators["profit_rate"] = profit_rate

            # Stop Loss 1: 고정 -3%
            if profit_rate <= cls.STOP_LOSS_FIXED:
                return {
                    "signal": "sell",
                    "strength": "strong",
                    "score": score,
                    "conditions": conditions,
                    "indicators": indicators,
                    "reason": f"손절: 고정 -3% 도달 (현재 {profit_rate*100:.2f}%)"
                }

            # Stop Loss 2: 20일 이평선 -5% 이탈
            if "ema_20" in indicators:
                ema_20_now = indicators["ema_20"]
                ema_deviation = (float(current_price) - ema_20_now) / ema_20_now
                if ema_deviation <= cls.STOP_LOSS_EMA_DEVIATION:
                    return {
                        "signal": "sell",
                        "strength": "strong",
                        "score": score,
                        "conditions": conditions,
                        "indicators": indicators,
                        "reason": f"손절: 20일 이평선 -5% 이탈 (현재 {ema_deviation*100:.2f}%)"
                    }

            # Profit Taking
            if profit_rate >= cls.PROFIT_TARGET_FIRST and current_signal in (1, 2):
                return {
                    "signal": "sell",
                    "strength": "weak",
                    "score": score,
                    "conditions": conditions,
                    "indicators": indicators,
                    "reason": f"익절: +5% 도달, 30% 익절 권장 (현재 {profit_rate*100:.2f}%)",
                    "sell_ratio": cls.PROFIT_RATIO_FIRST
                }

            if profit_rate >= cls.PROFIT_TARGET_SECOND and current_signal in (1, 2):
                return {
                    "signal": "sell",
                    "strength": "strong",
                    "score": score,
                    "conditions": conditions,
                    "indicators": indicators,
                    "reason": f"익절: +15% 도달, 70% 익절 권장 (현재 {profit_rate*100:.2f}%)",
                    "sell_ratio": cls.PROFIT_RATIO_SECOND
                }

        # Entry Signal (대기 상태에서만)
        if current_signal == 0:
            if score == 3:  # 3개 조건 모두 충족
                return {
                    "signal": "buy",
                    "strength": "strong",
                    "score": score,
                    "conditions": conditions,
                    "indicators": indicators,
                    "reason": "Strong Buy: 모멘텀 3개 조건 모두 충족"
                }

        # Hold
        return {
            "signal": "hold",
            "strength": None,
            "score": score,
            "conditions": conditions,
            "indicators": indicators,
            "reason": f"모멘텀 조건 미충족 (점수: {score}/3)"
        }
