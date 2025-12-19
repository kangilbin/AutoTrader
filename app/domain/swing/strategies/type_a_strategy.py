"""
TYPE A - Stable Entry Strategy (안정적 진입 전략)

Entry Conditions (all 4 must be met for Strong Buy):
1. 현재가 > 20일 이평선
2. 외국인/기관 순매수 > 0
3. 거래량 >= 전일 거래량 120%
4. RSI < 70 (과열 방지)

Scoring: 4개 모두 충족 = Strong, 3개 충족 = Weak

Risk Management:
- Stop Loss 1: 고정 -3% 손절
- Stop Loss 2: 20일 이평선 -5% 이탈
- Profit Taking: +5% 도달 시 30% 익절, +15% 도달 시 70% 익절
"""
import pandas as pd
import talib as ta
from typing import Dict, Tuple, Optional
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


class TypeAStrategy:
    """TYPE A - 안정적 진입 전략"""

    # 전략 파라미터
    EMA_PERIOD = 20  # 20일 이평선
    VOLUME_RATIO_THRESHOLD = 1.2  # 전일 대비 거래량 120%
    RSI_OVERBOUGHT = 70  # RSI 과열 기준
    RSI_PERIOD = 14

    STOP_LOSS_FIXED = -0.03  # 고정 -3% 손절
    STOP_LOSS_EMA_DEVIATION = -0.05  # 20일 이평선 -5% 이탈
    PROFIT_TARGET_FIRST = 0.05  # +5% 익절
    PROFIT_TARGET_SECOND = 0.15  # +15% 익절
    PROFIT_RATIO_FIRST = 0.3  # 5% 도달 시 30% 익절
    PROFIT_RATIO_SECOND = 0.7  # 15% 도달 시 70% 익절

    @classmethod
    def analyze(
        cls,
        df: pd.DataFrame,
        current_price: Decimal,
        frgn_ntby_qty: int,
        pgtr_ntby_qty: int,
        entry_price: Optional[Decimal] = None,
        current_signal: int = 0
    ) -> Dict:
        """
        TYPE A 전략 분석

        Args:
            df: 주가 데이터 (OHLCV)
            current_price: 현재가
            frgn_ntby_qty: 외국인 순매수량
            pgtr_ntby_qty: 기관 순매수량
            entry_price: 진입가 (손익 계산용, 포지션 있을 때)
            current_signal: 현재 신호 상태 (0, 1, 2, 3)

        Returns:
            {
                "signal": "buy" | "sell" | "hold",
                "strength": "strong" | "weak" | None,
                "score": int (0-4),
                "conditions": {
                    "price_above_ema": bool,
                    "net_buying_positive": bool,
                    "volume_sufficient": bool,
                    "rsi_not_overbought": bool
                },
                "indicators": {
                    "ema_20": float,
                    "rsi": float,
                    "volume_ratio": float,
                    "profit_rate": float (if entry_price provided)
                },
                "reason": str
            }
        """
        # 데이터 정렬 및 숫자형 변환
        if "STCK_BSOP_DATE" in df.columns:
            df = df.sort_values("STCK_BSOP_DATE").reset_index(drop=True)

        for col in ["STCK_CLPR", "ACML_VOL"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # 최소 데이터 길이 확인
        if len(df) < cls.EMA_PERIOD + 1:
            return {
                "signal": "hold",
                "strength": None,
                "score": 0,
                "conditions": {},
                "indicators": {},
                "reason": f"데이터 부족 (최소 {cls.EMA_PERIOD + 1}일 필요)"
            }

        close = df["STCK_CLPR"].values
        volume = df["ACML_VOL"].values

        # === 지표 계산 ===
        # 20일 EMA
        ema_20 = pd.Series(ta.EMA(close, timeperiod=cls.EMA_PERIOD), index=df.index)

        # RSI
        rsi = pd.Series(ta.RSI(close, timeperiod=cls.RSI_PERIOD), index=df.index)

        # NaN 체크
        if pd.isna(ema_20.iloc[-1]) or pd.isna(rsi.iloc[-1]):
            return {
                "signal": "hold",
                "strength": None,
                "score": 0,
                "conditions": {},
                "indicators": {},
                "reason": "지표 계산 불가 (NaN)"
            }

        ema_20_now = ema_20.iloc[-1]
        rsi_now = rsi.iloc[-1]
        current_volume = volume[-1]
        prev_volume = volume[-2] if len(volume) > 1 else current_volume

        # 전일 대비 거래량 비율
        volume_ratio = (current_volume / prev_volume) if prev_volume > 0 else 0

        # === Entry Conditions 평가 ===
        condition_1 = float(current_price) > ema_20_now  # 현재가 > 20일 이평선
        condition_2 = (frgn_ntby_qty > 0 or pgtr_ntby_qty > 0)  # 외국인 또는 기관 순매수
        condition_3 = volume_ratio >= cls.VOLUME_RATIO_THRESHOLD  # 거래량 120% 이상
        condition_4 = rsi_now < cls.RSI_OVERBOUGHT  # RSI < 70

        # 점수 계산
        score = sum([condition_1, condition_2, condition_3, condition_4])

        conditions = {
            "price_above_ema": condition_1,
            "net_buying_positive": condition_2,
            "volume_sufficient": condition_3,
            "rsi_not_overbought": condition_4
        }

        indicators = {
            "ema_20": float(ema_20_now),
            "rsi": float(rsi_now),
            "volume_ratio": float(volume_ratio),
        }

        # === Signal Generation ===

        # 포지션이 있는 경우 손익률 계산
        if entry_price:
            profit_rate = (float(current_price) - float(entry_price)) / float(entry_price)
            indicators["profit_rate"] = profit_rate

            # Exit Signals (Stop Loss / Profit Taking)
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

            # Profit Taking: +5% 도달 시 30% 익절
            if profit_rate >= cls.PROFIT_TARGET_FIRST and current_signal in (1, 2):
                return {
                    "signal": "sell",
                    "strength": "weak",  # 부분 익절
                    "score": score,
                    "conditions": conditions,
                    "indicators": indicators,
                    "reason": f"익절: +5% 도달, 30% 익절 권장 (현재 {profit_rate*100:.2f}%)",
                    "sell_ratio": cls.PROFIT_RATIO_FIRST
                }

            # Profit Taking: +15% 도달 시 70% 익절
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

        # Entry Signals
        if current_signal == 0:  # 대기 상태에서만 매수
            if score == 4:
                return {
                    "signal": "buy",
                    "strength": "strong",
                    "score": score,
                    "conditions": conditions,
                    "indicators": indicators,
                    "reason": "Strong Buy: 4개 조건 모두 충족"
                }
            elif score == 3:
                return {
                    "signal": "buy",
                    "strength": "weak",
                    "score": score,
                    "conditions": conditions,
                    "indicators": indicators,
                    "reason": "Weak Buy: 3개 조건 충족"
                }

        # Hold
        return {
            "signal": "hold",
            "strength": None,
            "score": score,
            "conditions": conditions,
            "indicators": indicators,
            "reason": f"진입 조건 미충족 (점수: {score}/4)"
        }
