"""
단일 20EMA 전략 베이스 클래스 (Base Single EMA Strategy)

백테스팅과 실전 전략의 공통 로직을 포함합니다.
- 공통 파라미터
- EOD 신호 체크 메서드
- 하락장 필터
- 2차 매수 조건 파라미터
"""
import pandas as pd
from typing import Optional


class BaseSingleEMAStrategy:
    """단일 20EMA 전략 공통 베이스 클래스"""

    # ========================================
    # 공통 파라미터
    # ========================================

    # EMA 기간
    EMA_PERIOD = 20
    EMA_LONG_PERIOD = 120  # 장기 EMA (하락장 판단용)

    # 매수 조건
    OBV_Z_BUY_THRESHOLD = 1.0
    OBV_LOOKBACK = 7
    MAX_GAP_RATIO = 0.05
    MAX_SURGE_RATIO = 0.05

    # 2차 매수 조건
    # [시나리오 A] 추세 강화형 (EMA-ATR 가드레일)
    TREND_BUY_ATR_LOWER = 0.3        # 하한: EMA + ATR × 0.3 (추세 가속 최소선)
    TREND_BUY_ATR_UPPER = 2.0        # 상한: EMA + ATR × 2.0 (과열 방지선)
    TREND_BUY_OBV_THRESHOLD = 1.2    # OBV z-score 최소값
    TREND_BUY_ADX_MIN = 25           # ADX 최소값 (강한 추세)

    # [시나리오 B] 눌림목 반등 (EMA-ATR 가드레일)
    PULLBACK_BUY_ATR_LOWER = -0.5    # 하한: EMA - ATR × 0.5 (조정 허용 하한)
    PULLBACK_BUY_ATR_UPPER = 0.3     # 상한: EMA + ATR × 0.3 (조정 범위 상한)
    PULLBACK_BUY_OBV_MIN = 0.0       # OBV z-score 최소값 (중립 이상)
    PULLBACK_BUY_ADX_MIN = 18        # ADX 하한 (추세 유지)
    PULLBACK_BUY_ADX_MAX = 23        # ADX 상한 (조정 구간)
    PULLBACK_BUY_REBOUND_RATIO = 1.004  # 저점 대비 반등 비율 (0.4%)

    # 매도 조건
    # [1차 방어선] 장중 손절
    ATR_MULTIPLIER = 1.0

    # [2차 방어선] EOD 신호
    EOD_SIGNAL_WINDOW_DAYS = 3       # EOD 신호 유효 기간 (3일)
    EOD_TREND_WEAK_DAYS = 2          # 추세 약화 연속 일수
    EOD_SUPPLY_WEAK_OBV_Z = -1.0     # OBV z-score 수급 약화 기준

    # ========================================
    # 공통 메서드: EOD 신호 체크
    # ========================================

    @staticmethod
    def _check_ema_breach_eod(row: pd.Series) -> bool:
        """
        EOD 신호 1: 종가가 EMA 아래로 하회했는지 체크

        Args:
            row: 일일 데이터 (종가, ema_20 포함)

        Returns:
            True: 종가 < EMA20
        """
        if pd.isna(row.get('ema_20')):
            return False
        return row['STCK_CLPR'] < row['ema_20']

    @staticmethod
    def _check_trend_weakness_eod(row: pd.Series, prev_row: pd.Series) -> bool:
        """
        EOD 신호 2: ADX/DMI 추세가 2일 연속 약화되었는지 체크

        Args:
            row: 오늘 데이터
            prev_row: 어제 데이터

        Returns:
            True: ADX < 20 AND -DI > +DI (2일 연속)
        """
        required_cols = ['adx', 'minus_di', 'plus_di']

        # NaN 체크
        if any(pd.isna(row.get(col)) for col in required_cols):
            return False
        if any(pd.isna(prev_row.get(col)) for col in required_cols):
            return False

        # 2일 연속 약세 체크
        is_today_weak = row['adx'] < 20 and row['minus_di'] > row['plus_di']
        is_yesterday_weak = prev_row['adx'] < 20 and prev_row['minus_di'] > prev_row['plus_di']

        return is_today_weak and is_yesterday_weak

    @classmethod
    def _check_supply_weakness_eod(cls, row: pd.Series) -> bool:
        """
        EOD 신호 3: 일일 수급이 약화되었는지 체크 (OBV z-score 기준)

        Args:
            row: 일일 데이터 (obv_z 포함)

        Returns:
            True: OBV z-score < -1.0
        """
        if pd.isna(row.get('obv_z')):
            return False
        return row['obv_z'] < cls.EOD_SUPPLY_WEAK_OBV_Z

    # ========================================
    # 공통 메서드: 하락장 필터
    # ========================================

    @staticmethod
    def _is_bearish_market(row: pd.Series) -> bool:
        """
        하락장 판단: 20 EMA가 120 EMA 아래로 내려간 경우

        Args:
            row: 일일 데이터 (ema_20, ema_120 포함)

        Returns:
            True: 하락장 (매수 금지)
            False: 상승장/횡보장 (매수 허용)
        """
        if pd.isna(row.get('ema_20')) or pd.isna(row.get('ema_120')):
            return False  # 지표 부족 시 매수 허용 (초기 데이터)

        return row['ema_20'] < row['ema_120']