"""
기술 지표 계산 유틸리티

Single EMA 전략에서 사용하는 기술 지표들을 계산합니다.
실전 매매와 백테스팅 모두에서 사용 가능하도록 범용적으로 설계되었습니다.
"""
import numpy as np
import pandas as pd
import talib as ta
from typing import Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class TechnicalIndicators:
    """기술 지표 계산 클래스"""

    @staticmethod
    def calculate_ema(
        close: np.ndarray,
        period: int = 20
    ) -> Optional[np.ndarray]:
        """
        EMA (Exponential Moving Average) 계산

        Args:
            close: 종가 배열
            period: EMA 기간 (기본값: 20)

        Returns:
            EMA 배열 또는 None (계산 실패 시)
        """
        if len(close) < period:
            return None

        try:
            ema = ta.EMA(close, timeperiod=period)
            return ema
        except Exception as e:
            logger.error(f"EMA 계산 실패: {e}")
            return None

    @staticmethod
    def calculate_atr(
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        period: int = 14
    ) -> Optional[np.ndarray]:
        """
        ATR (Average True Range) 계산

        Args:
            high: 고가 배열
            low: 저가 배열
            close: 종가 배열
            period: ATR 기간 (기본값: 14)

        Returns:
            ATR 배열 또는 None (계산 실패 시)
        """
        if len(high) < period or len(low) < period or len(close) < period:
            return None

        try:
            atr = ta.ATR(high, low, close, timeperiod=period)
            return atr
        except Exception as e:
            logger.error(f"ATR 계산 실패: {e}")
            return None

    @staticmethod
    def calculate_adx_dmi(
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        period: int = 14
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[np.ndarray]]:
        """
        ADX, +DI, -DI 계산

        Args:
            high: 고가 배열
            low: 저가 배열
            close: 종가 배열
            period: 계산 기간 (기본값: 14)

        Returns:
            (ADX 배열, +DI 배열, -DI 배열) 또는 (None, None, None)
        """
        if len(high) < period or len(low) < period or len(close) < period:
            return None, None, None

        try:
            adx = ta.ADX(high, low, close, timeperiod=period)
            plus_di = ta.PLUS_DI(high, low, close, timeperiod=period)
            minus_di = ta.MINUS_DI(high, low, close, timeperiod=period)
            return adx, plus_di, minus_di
        except Exception as e:
            logger.error(f"ADX/DMI 계산 실패: {e}")
            return None, None, None

    @staticmethod
    def calculate_realtime_ema_from_cache(
        yesterday_ema: float,
        current_price: float,
        period: int = 20
    ) -> float:
        """
        캐시된 어제 EMA를 활용한 실시간 EMA 증분 계산

        EMA 공식: EMA_today = (Price × K) + (EMA_yesterday × (1 - K))
        where K = 2 / (period + 1)

        Args:
            yesterday_ema: Redis 캐시에서 가져온 어제 EMA 값
            current_price: 현재가 (실시간)
            period: EMA 기간 (기본값: 20)

        Returns:
            오늘 실시간 EMA 값
        """
        k = 2.0 / (period + 1)  # smoothing factor (20일 EMA: k ≈ 0.095)
        return (current_price * k) + (yesterday_ema * (1 - k))

    @staticmethod
    def calculate_gap_ratio(
        price: float,
        ema: float
    ) -> float:
        """
        EMA 괴리율 계산

        Args:
            price: 현재가
            ema: EMA 값

        Returns:
            괴리율 (소수, 예: 0.05 = 5%)
        """
        if ema == 0:
            return 0.0
        return (price - ema) / ema

    @staticmethod
    def calculate_obv(
        close: np.ndarray,
        volume: np.ndarray
    ) -> Optional[np.ndarray]:
        """
        OBV (On Balance Volume) 계산

        Args:
            close: 종가 배열
            volume: 거래량 배열

        Returns:
            OBV 배열 또는 None (계산 실패 시)
        """
        if len(close) < 2 or len(volume) < 2:
            return None

        try:
            obv = ta.OBV(close, volume)
            return obv
        except Exception as e:
            logger.error(f"OBV 계산 실패: {e}")
            return None

    @staticmethod
    def calculate_obv_zscore(
        obv: np.ndarray,
        lookback: int = 7
    ) -> Optional[np.ndarray]:
        """
        OBV z-score 계산

        OBV의 변화량(diff)을 기준으로 z-score를 계산합니다.
        z-score = (현재 변화량 - 평균 변화량) / 표준편차

        Args:
            obv: OBV 배열
            lookback: z-score 계산 기간 (기본값: 7)

        Returns:
            OBV z-score 배열 또는 None (계산 실패 시)
        """
        if len(obv) < lookback:
            return None

        try:
            # pandas Series로 변환
            obv_series = pd.Series(obv)

            # OBV 변화량 계산
            obv_diff = obv_series.diff()

            # 롤링 평균 및 표준편차
            obv_mean = obv_diff.rolling(lookback, min_periods=3).mean()
            obv_std = obv_diff.rolling(lookback, min_periods=3).std()

            # 0으로 나누는 것 방지
            epsilon = 1e-9
            obv_std = obv_std.replace(0, epsilon).fillna(epsilon)

            # z-score 계산
            obv_z = ((obv_diff - obv_mean) / obv_std).fillna(0.0)

            return obv_z.values
        except Exception as e:
            logger.error(f"OBV z-score 계산 실패: {e}")
            return None

    @staticmethod
    def calculate_foreign_ratio(
        foreign_net_buy: int,
        volume: int
    ) -> float:
        """
        외국인 순매수 비율 계산

        Args:
            foreign_net_buy: 외국인 순매수량
            volume: 총 거래량

        Returns:
            외국인 순매수 비율 (%, 예: 1.5 = 1.5%)
        """
        if volume == 0:
            return 0.0
        return (foreign_net_buy / volume) * 100

    @classmethod
    def prepare_indicators_from_df(
        cls,
        df: pd.DataFrame,
        ema_period: int = 20,
        atr_period: int = 14,
        adx_period: int = 14,
        obv_lookback: int = 7
    ) -> pd.DataFrame:
        """
        DataFrame에 모든 지표 추가

        Args:
            df: OHLCV 데이터 (STCK_HGPR, STCK_LWPR, STCK_CLPR, ACML_VOL 필요)
            ema_period: EMA 기간
            atr_period: ATR 기간
            adx_period: ADX/DMI 기간
            obv_lookback: OBV z-score 계산 기간

        Returns:
            지표가 추가된 DataFrame
        """
        df = df.copy()

        # 데이터 추출
        high = df["STCK_HGPR"].values.astype(float)
        low = df["STCK_LWPR"].values.astype(float)
        close = df["STCK_CLPR"].values.astype(float)
        volume = df["ACML_VOL"].values.astype(float)

        # EMA
        ema = cls.calculate_ema(close, ema_period)
        if ema is not None:
            df["ema_20"] = ema
            # 괴리율
            df["gap_ratio"] = (close - ema) / ema

        # ATR
        atr = cls.calculate_atr(high, low, close, atr_period)
        if atr is not None:
            df["atr"] = atr

        # ADX, DMI
        adx, plus_di, minus_di = cls.calculate_adx_dmi(high, low, close, adx_period)
        if adx is not None:
            df["adx"] = adx
            df["plus_di"] = plus_di
            df["minus_di"] = minus_di

        # OBV
        obv = cls.calculate_obv(close, volume)
        if obv is not None:
            df["obv"] = obv

            # OBV z-score
            obv_z = cls.calculate_obv_zscore(obv, obv_lookback)
            if obv_z is not None:
                df["obv_z"] = obv_z

        # 외국인 비율 (컬럼이 있는 경우)
        if "FRGN_NTBY_QTY" in df.columns:
            df["frgn_ratio"] = df.apply(
                lambda row: cls.calculate_foreign_ratio(
                    row["FRGN_NTBY_QTY"],
                    row["ACML_VOL"]
                ),
                axis=1
            )

        return df

    @classmethod
    def get_realtime_indicators(
        cls,
        df: pd.DataFrame,
        current_price: float,
        current_high: Optional[float] = None,
        current_low: Optional[float] = None,
        current_volume: Optional[int] = None,
        ema_period: int = 20
    ) -> dict:
        """
        실시간 지표 계산 (현재가 포함)

        Args:
            df: 과거 OHLCV 데이터
            current_price: 현재가
            current_high: 현재 고가 (선택)
            current_low: 현재 저가 (선택)
            current_volume: 현재 거래량 (선택)
            ema_period: EMA 기간

        Returns:
            {
                "ema_20": float,
                "gap_ratio": float,
                "atr": float,
                "adx": float,
                "plus_di": float,
                "minus_di": float,
                "obv_z": float
            }
        """
        result = {}

        # 과거 종가 배열
        close_prices = df["STCK_CLPR"].values.astype(float)

        # 현재가 추가
        close_with_current = np.append(close_prices, current_price)

        # 실시간 EMA
        ema = cls.calculate_ema(close_with_current, ema_period)
        if ema is not None and len(ema) > 0:
            result["ema_20"] = float(ema[-1])
            result["gap_ratio"] = cls.calculate_gap_ratio(current_price, ema[-1])

        # ATR (고가/저가 필요, 실시간 계산 어려움)
        if current_high is not None and current_low is not None:
            high = np.append(df["STCK_HGPR"].values.astype(float), current_high)
            low = np.append(df["STCK_LWPR"].values.astype(float), current_low)
            atr = cls.calculate_atr(high, low, close_with_current, 14)
            if atr is not None and len(atr) > 0:
                result["atr"] = float(atr[-1])
        else:
            # 어제 ATR 사용
            if "atr" in df.columns and len(df) > 0:
                result["atr"] = float(df["atr"].iloc[-1])

        # ADX, DMI (실시간 계산 어려움, 어제 값 사용)
        if "adx" in df.columns and len(df) > 0:
            result["adx"] = float(df["adx"].iloc[-1])
            result["plus_di"] = float(df["plus_di"].iloc[-1])
            result["minus_di"] = float(df["minus_di"].iloc[-1])

        # OBV z-score (거래량 필요)
        if current_volume is not None and "obv" in df.columns:
            obv_prev = df["obv"].values
            # 간단한 OBV 업데이트 (정확하지 않음, 5분마다 재계산 권장)
            last_close = close_prices[-1]
            last_obv = obv_prev[-1]
            if current_price > last_close:
                current_obv = last_obv + current_volume
            elif current_price < last_close:
                current_obv = last_obv - current_volume
            else:
                current_obv = last_obv

            obv_with_current = np.append(obv_prev, current_obv)
            obv_z = cls.calculate_obv_zscore(obv_with_current, 7)
            if obv_z is not None and len(obv_z) > 0:
                result["obv_z"] = float(obv_z[-1])
        elif "obv_z" in df.columns and len(df) > 0:
            # 어제 값 사용
            result["obv_z"] = float(df["obv_z"].iloc[-1])

        return result